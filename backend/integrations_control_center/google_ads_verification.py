"""Version-aware independent read-back and unique-label creation recovery.

Only observable requested fields can pass. Scalar omission is a protobuf default
only when the exact selected leaf metadata proves it. Upload verification covers
the frozen identity/name and asset type, plus decoded dimensions for images;
the gateway retains the provider receipt and frozen request hash. Input-only
image and media-bundle bytes cannot be read back for byte equality. This module
never writes or retries a mutation.
"""
from __future__ import annotations

import base64
import hashlib
import io
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from .google_ads_client import gaql_string, owned_resource, problem

MAX_OPERATIONS = 10_000
MAX_QUERY_TARGETS = 500
MAX_RECOVERY_ROWS = 2000
_MISSING = object()
_ROOTS = {
    "campaignBudgetOperation": ("campaignBudget", "campaignBudgets"),
    "campaignOperation": ("campaign", "campaigns"),
    "campaignCriterionOperation": ("campaignCriterion", "campaignCriteria"),
    "campaignConversionGoalOperation": ("campaignConversionGoal", "campaignConversionGoals"),
    "assetOperation": ("asset", "assets"),
    "adGroupOperation": ("adGroup", "adGroups"),
    "adGroupAdOperation": ("adGroupAd", "adGroupAds"),
    "adGroupCriterionOperation": ("adGroupCriterion", "adGroupCriteria"),
    "adGroupAssetOperation": ("adGroupAsset", "adGroupAssets"),
    "assetGroupOperation": ("assetGroup", "assetGroups"),
    "assetGroupAssetOperation": ("assetGroupAsset", "assetGroupAssets"),
    "assetGroupSignalOperation": ("assetGroupSignal", "assetGroupSignals"),
    "assetGroupListingGroupFilterOperation": ("assetGroupListingGroupFilter", "assetGroupListingGroupFilters"),
    "campaignAssetOperation": ("campaignAsset", "campaignAssets"),
    "labelOperation": ("label", "labels"),
    "campaignLabelOperation": ("campaignLabel", "campaignLabels"),
}
_BIDDING_UNIONS = {
    "manualCpc": "MANUAL_CPC", "manualCpm": "MANUAL_CPM", "percentCpc": "PERCENT_CPC",
    "targetSpend": "TARGET_SPEND", "maximizeConversions": "MAXIMIZE_CONVERSIONS",
    "maximizeConversionValue": "MAXIMIZE_CONVERSION_VALUE", "targetCpa": "TARGET_CPA", "targetRoas": "TARGET_ROAS",
}
_BIDDING_DEFAULTS = {
    ("manualCpc", "enhancedCpcEnabled"): False,
    ("targetSpend", "cpcBidCeilingMicros"): "0",
    ("percentCpc", "cpcBidCeilingMicros"): "0",
    ("maximizeConversions", "targetCpaMicros"): "0",
    ("maximizeConversionValue", "targetRoas"): 0,
}
_AD_UNIONS = {
    "shoppingProductAd": "SHOPPING_PRODUCT_AD", "hotelAd": "HOTEL_AD",
    "responsiveSearchAd": "RESPONSIVE_SEARCH_AD", "expandedDynamicSearchAd": "EXPANDED_DYNAMIC_SEARCH_AD",
    "responsiveDisplayAd": "RESPONSIVE_DISPLAY_AD", "imageAd": "IMAGE_AD", "displayUploadAd": "DISPLAY_UPLOAD_AD",
    "demandGenMultiAssetAd": "DEMAND_GEN_MULTI_ASSET_AD", "demandGenVideoResponsiveAd": "DEMAND_GEN_VIDEO_RESPONSIVE_AD",
    "demandGenProductAd": "DEMAND_GEN_PRODUCT_AD", "demandGenCarouselAd": "DEMAND_GEN_CAROUSEL_AD",
    "appAd": "APP_AD", "appEngagementAd": "APP_ENGAGEMENT_AD", "appPreRegistrationAd": "APP_PRE_REGISTRATION_AD",
}
_UNORDERED_ARRAYS = {
    "headlines", "descriptions", "longHeadlines", "marketingImages", "squareMarketingImages",
    "portraitMarketingImages", "tallPortraitMarketingImages", "squareLogoImages", "logoImages", "videos",
    "youtubeVideos", "images", "assetAutomationSettings", "conversionActions", "optimizationGoalTypes",
}


def snake(value):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def _camel(value):
    first, *rest = value.split("_")
    return first + "".join(item[:1].upper() + item[1:] for item in rest)


def _leaves(value, path=()):
    if isinstance(value, dict) and value:
        for key, child in value.items():
            yield from _leaves(child, (*path, key))
    else:
        yield path, value


def _value(root, path):
    for key in path:
        if not isinstance(root, dict) or key not in root:
            return _MISSING
        root = root[key]
    return root


def _equal(expected, actual, path=()):
    if actual is _MISSING:
        return False
    if isinstance(expected, bool):
        return actual is expected
    if isinstance(actual, bool):
        return False
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return False
        if not path or path[-1] not in _UNORDERED_ARRAYS:
            return all(_equal(wanted, found, path) for wanted, found in zip(expected, actual))
        remaining = list(actual)
        for item in expected:
            for index, candidate in enumerate(remaining):
                if _equal(item, candidate, path):
                    remaining.pop(index)
                    break
            else:
                return False
        return not remaining
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        if not expected:
            return not actual
        return all(_equal(value, actual.get(key, _MISSING), (*path, key)) for key, value in expected.items())
    if expected == actual:
        return True
    if isinstance(expected, (int, float, Decimal)):
        try:
            wanted, found = Decimal(str(expected)), Decimal(str(actual))
            return wanted.is_finite() and found.is_finite() and wanted == found
        except (InvalidOperation, ValueError, TypeError):
            return False
    return False


def _replace(value, identities):
    if isinstance(value, dict):
        return {key: _replace(child, identities) for key, child in value.items()}
    if isinstance(value, list):
        return [_replace(child, identities) for child in value]
    if not isinstance(value, str):
        return value
    if value in identities:
        return identities[value]
    parts = value.split("/")
    if len(parts) != 4 or parts[0] != "customers" or "~" not in parts[-1]:
        return value
    replacements = defaultdict(set)
    for original, actual in identities.items():
        old, new = original.split("/"), actual.split("/")
        if len(old) == len(new) == 4 and re.fullmatch(r"-[0-9]+", old[-1]) and new[-1].isdigit():
            replacements[old[-1]].add(new[-1])
    suffix = []
    for component in parts[-1].split("~"):
        matches = replacements.get(component, set())
        if len(matches) > 1:
            raise problem("google_ads_temporary_identity_ambiguous", "هوية مؤقتة متعارضة؛ لا يمكن التحقق بأمان.")
        suffix.append(next(iter(matches)) if matches else component)
    return "/".join([*parts[:-1], "~".join(suffix)])


def _operation(operation):
    if not isinstance(operation, dict) or len(operation) != 1:
        raise problem("google_ads_verification_operation_invalid", "عقد عنصر التحقق غير صالح.", 422)
    key, spec = next(iter(operation.items()))
    if key not in _ROOTS or not isinstance(spec, dict):
        raise problem("google_ads_verification_operation_unsupported", "نوع عنصر التحقق غير مدعوم.", 422)
    actions = [action for action in ("create", "update", "remove") if action in spec]
    if len(actions) != 1 or actions[0] != "remove" and not isinstance(spec[actions[0]], dict):
        raise problem("google_ads_verification_operation_invalid", "عملية عنصر التحقق غير محددة.", 422)
    root, collection = _ROOTS[key]
    return key, root, collection, spec, actions[0]


def _field(root, path):
    return snake(root) + "." + ".".join(snake(key) for key in path)


def _upload_bytes(value):
    if not isinstance(value, str) or len(value) > 7_000_000:
        return None
    try:
        raw = base64.b64decode(value, validate=True)
        return raw if 0 < len(raw) <= 5 * 1024 * 1024 else None
    except (ValueError, TypeError):
        return None


def _image_dimensions(value):
    raw = _upload_bytes(value)
    if raw is None:
        return None
    try:
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as source:
            width, height = source.size
        return (width, height) if 0 < width <= 16384 and 0 < height <= 16384 else None
    except Exception:
        return None


def _asset_substantive_content(expected):
    """Comparable frozen content for receipt-backed duplicate result identities.

    Input-only upload hashes bind the frozen request, not Google response bytes.
    Different upload payloads cannot establish identical content from dimensions
    alone and therefore remain unresolved if their returned identities collide.
    """
    unions = [(key, kind) for key, kind in (
        ("imageAsset", "IMAGE"), ("textAsset", "TEXT"), ("youtubeVideoAsset", "YOUTUBE_VIDEO"),
        ("mediaBundleAsset", "MEDIA_BUNDLE"), ("demandGenCarouselCardAsset", "DEMAND_GEN_CAROUSEL_CARD"),
    ) if key in expected]
    if len(unions) != 1:
        return None
    union, kind = unions[0]
    content = expected[union]
    if not isinstance(content, dict) or not content or expected.get("type", kind) != kind:
        return None
    if kind in {"IMAGE", "MEDIA_BUNDLE"}:
        raw = _upload_bytes(content.get("data"))
        return {"kind": kind, "input_sha256": hashlib.sha256(raw).hexdigest()} if raw is not None else None
    if kind == "TEXT" and (not isinstance(content.get("text"), str) or not content["text"]):
        return None
    if kind == "YOUTUBE_VIDEO" and (not isinstance(content.get("youtubeVideoId"), str) or not content["youtubeVideoId"]):
        return None
    return {"kind": kind, "content": content}


def _checks(root, expected, spec=None, *, accepted_receipt=False):
    checks, gaps, scopes, seen = [], [], [], set()
    receipt_content = (_asset_substantive_content(expected)
                       if root == "asset" and accepted_receipt is True and spec and "create" in spec else None)
    optional_name = receipt_content is not None and receipt_content["kind"] in {"IMAGE", "TEXT"}

    def add(path, wanted, selector=None, kind="equal"):
        if (path, kind) not in seen:
            seen.add((path, kind))
            checks.append({"path": path, "wanted": wanted, "select_path": selector or path, "kind": kind})

    for path, value in _leaves(expected):
        if path == ("resourceName",):
            continue
        if path == ("name",) and optional_name:
            continue
        if root == "asset" and path == ("imageAsset", "data"):
            dimensions = _image_dimensions(value)
            add(("type",), "IMAGE")
            if dimensions is None:
                gaps.append("asset.image_asset:input_dimensions_unavailable")
            else:
                add(("imageAsset", "fullSize", "widthPixels"), dimensions[0])
                add(("imageAsset", "fullSize", "heightPixels"), dimensions[1])
            if not optional_name and (not isinstance(expected.get("name"), str) or not expected["name"]):
                gaps.append("asset.name:upload_frozen_identity_missing")
            raw = _upload_bytes(value)
            scopes.append({"kind": "image_upload", "independent_check": (
                               "resource_identity_type_dimensions" if optional_name else "resource_identity_name_type_dimensions"),
                           "byte_equality_verified": False, "requires_verified_upload_receipt": True,
                           "requires_frozen_request_hash": True, "optional_name_ignored": optional_name,
                           "frozen_input_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
                           "operator_preview_required": True})
            continue
        if root == "asset" and path == ("mediaBundleAsset", "data"):
            # ZIP validation is performed before the immutable proposal is
            # created. GAQL exposes no byte-equivalence proof for this input-
            # only field: retain its hash and verify the independent identity.
            raw = _upload_bytes(value)
            add(("type",), "MEDIA_BUNDLE")
            if raw is None:
                gaps.append("asset.media_bundle_asset:input_bytes_invalid")
            if not isinstance(expected.get("name"), str) or not expected["name"].strip():
                gaps.append("asset.name:upload_frozen_identity_missing")
            scopes.append({"kind": "media_bundle_upload", "independent_check": "resource_identity_name_type",
                           "byte_equality_verified": False, "requires_verified_upload_receipt": True,
                           "requires_frozen_request_hash": True,
                           "frozen_input_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None})
            continue
        if value is None:
            gaps.append(_field(root, path) + ":null_reset_unobservable")
        elif value == {}:
            if root == "campaign" and len(path) == 1 and path[0] in _BIDDING_UNIONS:
                add(("biddingStrategyType",), _BIDDING_UNIONS[path[0]])
            elif root == "adGroupAd" and len(path) == 2 and path[0] == "ad" and path[1] in _AD_UNIONS:
                add(("ad", "type"), _AD_UNIONS[path[1]])
            elif path[-1:] in {("productItemId",), ("hotelId",)} and "caseValue" in path:
                add(path, {}, selector=(*path, "value"))
            else:
                gaps.append(_field(root, path) + ":empty_message_unobservable")
        else:
            add(path, value)
    if optional_name and receipt_content["kind"] == "TEXT":
        scopes.append({"kind": "text_asset", "independent_check": "resource_identity_type_exact_text",
                       "optional_name_ignored": True, "requires_verified_upload_receipt": True})
    if root == "campaign":
        for union, value in _BIDDING_UNIONS.items():
            if union in expected:
                add(("biddingStrategyType",), value)
    if root == "adGroupAd":
        ad = expected.get("ad") if isinstance(expected.get("ad"), dict) else {}
        for union, value in _AD_UNIONS.items():
            if union in ad:
                add(("ad", "type"), value)
    if root == "asset":
        for union, value in (("textAsset", "TEXT"), ("youtubeVideoAsset", "YOUTUBE_VIDEO"),
                             ("demandGenCarouselCardAsset", "DEMAND_GEN_CAROUSEL_CARD"), ("mediaBundleAsset", "MEDIA_BUNDLE")):
            if union in expected:
                add(("type",), value)
    if spec and "update" in spec:
        mask = spec.get("updateMask")
        if not isinstance(mask, str) or not mask or any(not re.fullmatch(r"[a-z][A-Za-z0-9_]*(?:\.[a-z][A-Za-z0-9_]*)*", item) for item in mask.split(",")):
            gaps.append(snake(root) + ":update_mask_unverified")
        else:
            for item in mask.split(","):
                path = tuple(_camel(part) for part in item.split("."))
                if _value(expected, path) is not _MISSING:
                    continue
                if root == "campaign" and len(path) == 1 and path[0] in _BIDDING_UNIONS:
                    add(("biddingStrategyType",), _BIDDING_UNIONS[path[0]], kind="not_equal")
                elif root == "campaign" and path in _BIDDING_DEFAULTS and expected.get(path[0]) == {}:
                    add(path, _BIDDING_DEFAULTS[path])
                else:
                    gaps.append(_field(root, path) + ":masked_clear_unobservable")
    return checks, gaps, scopes


def _field_names(root, checks):
    names = {_field(root, ("resourceName",)), _field(root, ("status",))}
    for check in checks:
        for length in range(1, len(check["select_path"]) + 1):
            names.add(_field(root, check["select_path"][:length]))
    return names


async def _metadata(client, names, cache=None):
    cache = cache if cache is not None else {}
    needed = sorted(names - cache.keys())
    for offset in range(0, len(needed), 500):
        batch = needed[offset:offset + 500]
        response = await client.field_metadata(batch)
        if not isinstance(response, dict):
            raise problem("google_ads_field_metadata_invalid", "بيانات حقول القراءة غير مكتملة.", 502)
        for name in batch:
            value = response.get(name)
            cache[name] = value if isinstance(value, dict) else {}
    return cache


def _resolve(root, checks, metadata):
    resolved, selected, gaps = [], set(), []
    for check in checks:
        selected_field = None
        for length in range(1, len(check["select_path"]) + 1):
            candidate = _field(root, check["select_path"][:length])
            field = metadata.get(candidate, {})
            if field.get("selectable") is True:
                selected_field = candidate
                if field.get("isRepeated") is True or field.get("is_repeated") is True:
                    break
        if selected_field is None:
            gaps.append(_field(root, check["select_path"]))
            continue
        resolved.append({**check, "selected_field": selected_field})
        selected.add(selected_field)
    return resolved, selected, gaps


def _observed(root, after, check, metadata):
    actual = _value(after, check["path"])
    if actual is not _MISSING:
        return actual
    if check["selected_field"] != _field(root, check["path"]):
        return _MISSING
    field, wanted = metadata.get(check["selected_field"], {}), check["wanted"]
    kind = field.get("dataType") or field.get("data_type")
    if check["kind"] != "equal":
        return _MISSING
    if wanted is False and kind == "BOOLEAN":
        return False
    if not isinstance(wanted, bool) and wanted in (0, "0") and kind in {"INT32", "INT64", "UINT64", "DOUBLE", "FLOAT"}:
        return 0
    if wanted == "" and kind == "STRING":
        return ""
    if wanted == [] and (field.get("isRepeated") is True or field.get("is_repeated") is True):
        return []
    return _MISSING


def _matches(root, after, resolved, metadata):
    failures = []
    for check in resolved:
        actual = _observed(root, after, check, metadata)
        wanted = check["wanted"]
        field_kind = metadata.get(_field(root, check["path"]), {}).get("dataType") or metadata.get(_field(root, check["path"]), {}).get("data_type")
        if isinstance(wanted, str) and field_kind in {"INT32", "INT64", "UINT64", "DOUBLE", "FLOAT"}:
            try:
                wanted = Decimal(wanted)
            except InvalidOperation:
                pass
        matched = _equal(wanted, actual, check["path"])
        if check["kind"] == "not_equal":
            matched = actual is not _MISSING and actual is not None and not matched
        if not matched:
            failures.append(".".join(check["path"]))
    return failures


def _error_code(exc):
    detail = getattr(exc, "detail", None)
    value = detail.get("code") if isinstance(detail, dict) else type(exc).__name__
    return str(value) if re.fullmatch(r"[A-Za-z0-9_]{1,100}", str(value)) else "read_error"


def _lookup(root, rows, collection, customer_id):
    lookup, invalid = {}, False
    for row in rows:
        value = row.get(root) if isinstance(row, dict) else None
        if not isinstance(value, dict) or not isinstance(value.get("resourceName"), str):
            invalid = True
            continue
        name = owned_resource(value["resourceName"], customer_id, collection)
        if name in lookup:
            invalid = True
        lookup[name] = value
    return lookup, invalid


async def verify_google_operations(client, operations, resource_names, *, accepted_receipt=False):
    """Verify bounded readback; receipt trust is asserted only by the gateway.

    ``accepted_receipt=True`` requires a persisted successful atomic provider
    response whose ordered resource identities and frozen operations digest
    match this request. It permits Google's immutable asset deduplication; it
    does not bypass content, type, identity, or metadata-backed field checks.
    """
    if (not isinstance(operations, list) or not isinstance(resource_names, list)
        or not 1 <= len(operations) <= MAX_OPERATIONS or len(operations) != len(resource_names)):
        raise problem("google_ads_verification_identity_missing", "هويات التحقق غير مكتملة.")
    groups, identities, all_names = defaultdict(list), {}, set()
    mismatches, verified, gaps, scopes = [], [], [], []
    descriptors = [_operation(operation) for operation in operations]
    for (_, _, collection, spec, _), actual_name in zip(descriptors, resource_names):
        owned_resource(actual_name, client.customer_id, collection)
        original = (spec.get("create") or {}).get("resourceName")
        if original:
            if original in identities and identities[original] != actual_name:
                raise problem("google_ads_verification_identity_ambiguous", "هوية مورد الإنشاء متعارضة.")
            identities[original] = actual_name
    collisions = defaultdict(list)
    for index, name in enumerate(resource_names):
        collisions[name].append(index)
    for indexes in collisions.values():
        if len(indexes) < 2:
            continue
        contents = []
        allowed = accepted_receipt is True
        for index in indexes:
            key, _, _, spec, action = descriptors[index]
            if key != "assetOperation" or action != "create":
                allowed = False
                break
            content = _asset_substantive_content(spec["create"])
            if content is None:
                allowed = False
                break
            contents.append(content)
        if not allowed or not all(_equal(contents[0], content) for content in contents[1:]):
            gaps.append("duplicate_operation_resource_identity")
    for (_, root, collection, spec, action), actual_name in zip(descriptors, resource_names):
        expected = _replace(spec.get(action) if action != "remove" else {}, identities)
        original = spec.get("remove") if action == "remove" else expected.get("resourceName")
        if action != "create" and original != actual_name:
            mismatches.append({"resource_name": actual_name, "field": "operation_identity"})
        checks, local_gaps, local_scopes = (_checks(root, expected, spec, accepted_receipt=accepted_receipt)
                                          if action != "remove" else ([], [], []))
        scopes.extend({"resource_name": actual_name, **scope} for scope in local_scopes)
        all_names |= _field_names(root, checks)
        groups[root].append({"name": actual_name, "collection": collection, "checks": checks,
                             "remove": action == "remove", "gaps": local_gaps,
                             "identity_matches": action == "create" or original == actual_name})
    try:
        metadata = await _metadata(client, all_names)
    except Exception as exc:
        gaps.append("field_metadata_read_failed:" + _error_code(exc))
        metadata = {}
    for root, targets in groups.items():
        root_field, name_field = snake(root), _field(root, ("resourceName",))
        if metadata.get(name_field, {}).get("selectable") is not True:
            gaps.append(name_field)
            continue
        for offset in range(0, len(targets), MAX_QUERY_TARGETS):
            batch = targets[offset:offset + MAX_QUERY_TARGETS]
            selected = {name_field}
            for target in batch:
                target["resolved"], fields, missing = _resolve(root, target["checks"], metadata)
                target["gaps"].extend(missing)
                gaps.extend(target["gaps"])
                selected |= fields
                if target["remove"] and metadata.get(_field(root, ("status",)), {}).get("selectable") is True:
                    selected.add(_field(root, ("status",)))
            wanted_names = {target["name"] for target in batch}
            query = ("SELECT " + ", ".join(sorted(selected)) + " FROM " + root_field + " WHERE " + name_field
                     + " IN (" + ",".join(gaql_string(name) for name in sorted(wanted_names)) + ")")
            try:
                rows, complete = await client.search(query, max_rows=len(wanted_names))
                lookup, invalid = _lookup(root, rows, batch[0]["collection"], client.customer_id)
                if invalid:
                    gaps.append(root_field + ":ambiguous_or_invalid_rows")
                if not complete:
                    gaps.append(root_field + ":incomplete_readback")
            except Exception as exc:
                gaps.append(root_field + ":readback_failed:" + _error_code(exc))
                continue
            if set(lookup) - wanted_names:
                gaps.append(root_field + ":unexpected_resource_identity")
                invalid = True
            for target in batch:
                after = lookup.get(target["name"])
                if target["remove"]:
                    if after is not None and after.get("status") != "REMOVED":
                        mismatches.append({"resource_name": target["name"], "field": "status"})
                    elif complete and not invalid and not target["gaps"] and target["identity_matches"]:
                        verified.append(target["name"])
                    continue
                if after is None:
                    mismatches.append({"resource_name": target["name"], "field": "resource_name"})
                    continue
                failures = _matches(root, after, target["resolved"], metadata)
                for field in failures:
                    mismatches.append({"resource_name": target["name"], "field": field})
                if complete and not invalid and not target["gaps"] and not failures and target["identity_matches"]:
                    verified.append(target["name"])
    report = {"verified_resources": verified, "mismatches": mismatches[:100], "coverage_gaps": sorted(set(gaps))[:100],
              "readback_independent": True, "verification_scope": {
                  "mutable_fields": "exact_selected_fields",
                  "image_uploads": [scope for scope in scopes if scope["kind"] == "image_upload"],
                  "media_bundle_uploads": [scope for scope in scopes if scope["kind"] == "media_bundle_upload"],
                  "text_assets": [scope for scope in scopes if scope["kind"] == "text_asset"],
              }}
    return not mismatches and not gaps and len(verified) == len(operations), report


def _recovery_problem(code, **evidence):
    return problem(code, "نتيجة إنشاء جوجل غير محسومة؛ يلزم استكمال القراءة دون إعادة الإنشاء.", **evidence)


async def _read_one(client, root, collection, fields, where, metadata):
    fields.add(_field(root, ("resourceName",)))
    await _metadata(client, fields, metadata)
    missing = [field for field in fields if metadata.get(field, {}).get("selectable") is not True]
    if missing:
        raise _recovery_problem("google_ads_recovery_field_gap", fields=sorted(missing))
    rows, complete = await client.search("SELECT " + ", ".join(sorted(fields)) + " FROM " + snake(root) + " WHERE " + where, max_rows=MAX_RECOVERY_ROWS)
    lookup, invalid = _lookup(root, rows, collection, client.customer_id)
    if not complete or invalid:
        raise _recovery_problem("google_ads_recovery_read_incomplete")
    return lookup


async def recover_create_operation_names(client, operations, correlation_label_name, *, known_before_resource_names=None):
    """Recover one atomic graph, then let the caller verify all recovered names.

    Only the exact provider correlation label anchors the campaign. Ambiguous
    candidates, absent field coverage and uncorrelated global assets fail closed.
    """
    if (not isinstance(operations, list) or not 1 <= len(operations) <= MAX_OPERATIONS
        or not isinstance(correlation_label_name, str) or not re.fullmatch(r"MEZAN-[A-Za-z0-9_-]{8,128}", correlation_label_name)):
        raise _recovery_problem("google_ads_recovery_correlation_invalid")
    descriptors = [_operation(operation) for operation in operations]
    if all(key == "assetOperation" and action == "create" for key, _, _, _, action in descriptors):
        # An account-global asset's unique frozen name is its own correlation
        # marker. Standalone asset uploads do not need a campaign label.
        try:
            return await _recover_pending(client, descriptors, [None] * len(operations), {}, {},
                                          None, correlation_label_name, set())
        except Exception as exc:
            if str(_error_code(exc)).startswith("google_ads_recovery_"):
                raise
            raise _recovery_problem("google_ads_recovery_read_failed", reason=_error_code(exc)) from None
    campaign_indices = [i for i, (key, _, _, _, action) in enumerate(descriptors) if key == "campaignOperation" and action == "create"]
    label_indices = [i for i, (key, _, _, spec, action) in enumerate(descriptors) if key == "labelOperation" and action == "create" and spec["create"].get("name") == correlation_label_name]
    link_indices = [i for i, (key, _, _, _, action) in enumerate(descriptors) if key == "campaignLabelOperation" and action == "create"]
    if len(campaign_indices) > 1 or len(label_indices) != 1 or len(link_indices) != 1:
        raise _recovery_problem("google_ads_recovery_correlation_invalid")
    campaign_index = campaign_indices[0] if campaign_indices else None
    label_index, link_index = label_indices[0], link_indices[0]
    label_create, link_create = (descriptors[index][3]["create"] for index in (label_index, link_index))
    campaign_create = descriptors[campaign_index][3]["create"] if campaign_index is not None else {}
    if (not label_create.get("resourceName") or link_create.get("label") != label_create["resourceName"]
        or campaign_index is not None and (not campaign_create.get("resourceName") or link_create.get("campaign") != campaign_create["resourceName"])):
        raise _recovery_problem("google_ads_recovery_correlation_invalid")
    before = set()
    if campaign_index is None:
        if not isinstance(known_before_resource_names, list):
            raise _recovery_problem("google_ads_recovery_existing_scope_checkpoint_missing")
        before = {owned_resource(name, client.customer_id) for name in known_before_resource_names}
        owned_resource(link_create.get("campaign"), client.customer_id, "campaigns")
    elif any(action == "remove" for _, _, _, _, action in descriptors):
        raise _recovery_problem("google_ads_recovery_remove_scope_unproved")
    metadata = {}
    try:
        correlated = await _read_one(client, "campaignLabel", "campaignLabels", {
            "campaign_label.campaign", "campaign_label.label", "label.name", "label.resource_name",
        }, "label.name = " + gaql_string(correlation_label_name), metadata)
        if len(correlated) != 1:
            raise _recovery_problem("google_ads_recovery_correlation_missing" if not correlated else "google_ads_recovery_correlation_ambiguous")
        link_name, link = next(iter(correlated.items()))
        campaign_name = owned_resource(link.get("campaign"), client.customer_id, "campaigns")
        if campaign_index is None and campaign_name != link_create["campaign"]:
            raise _recovery_problem("google_ads_recovery_scope_mismatch")
        label_name = owned_resource(link.get("label"), client.customer_id, "labels")
        labels = await _read_one(client, "label", "labels", {"label.name"}, "label.resource_name = " + gaql_string(label_name), metadata)
        if len(labels) != 1 or labels.get(label_name, {}).get("name") != correlation_label_name:
            raise _recovery_problem("google_ads_recovery_correlation_unproved")
        suffix = campaign_name.rsplit("/", 1)[-1] + "~" + label_name.rsplit("/", 1)[-1]
        if link_name != f"customers/{client.customer_id}/campaignLabels/{suffix}":
            raise _recovery_problem("google_ads_recovery_correlation_unproved")
        names = [None] * len(operations)
        names[label_index], names[link_index] = label_name, link_name
        identities = {label_create["resourceName"]: label_name}
        if campaign_index is not None:
            names[campaign_index] = campaign_name
            identities[campaign_create["resourceName"]] = campaign_name
            campaigns = await _read_one(client, "campaign", "campaigns", {"campaign.campaign_budget"}, "campaign.resource_name = " + gaql_string(campaign_name), metadata)
            if len(campaigns) != 1:
                raise _recovery_problem("google_ads_recovery_campaign_missing")
            budget_name = owned_resource(campaigns[campaign_name].get("campaignBudget"), client.customer_id, "campaignBudgets")
            budget_indices = [i for i, (key, _, _, spec, action) in enumerate(descriptors)
                              if key == "campaignBudgetOperation" and action == "create" and spec["create"].get("resourceName") == campaign_create.get("campaignBudget")]
            if len(budget_indices) != 1:
                raise _recovery_problem("google_ads_recovery_budget_identity_missing")
            budget_index = budget_indices[0]
            names[budget_index] = budget_name
            identities[descriptors[budget_index][3]["create"]["resourceName"]] = budget_name
        return await _recover_pending(client, descriptors, names, identities, metadata,
                                      campaign_name, correlation_label_name, before)
    except Exception as exc:
        if str(_error_code(exc)).startswith("google_ads_recovery_"):
            raise
        raise _recovery_problem("google_ads_recovery_read_failed", reason=_error_code(exc)) from None


async def _recover_pending(client, descriptors, names, identities, metadata, campaign_name, correlation_label_name, before):
    pending = {index for index, name in enumerate(names) if name is None}
    while pending:
        progress = False
        for index in sorted(pending):
            _, root, collection, spec, action = descriptors[index]
            expected = _replace(spec[action], identities)
            if _has_temporary_reference(expected, ignore_root_name=action == "create"):
                continue
            if action == "remove":
                target = owned_resource(expected, client.customer_id, collection)
                if target not in before:
                    raise _recovery_problem("google_ads_recovery_remove_scope_unproved", operation_index=index)
                names[index] = target
                pending.remove(index)
                progress = True
                continue
            parent = expected.get("adGroup") or expected.get("assetGroup")
            if parent and parent not in before and parent not in identities.values():
                raise _recovery_problem("google_ads_recovery_parent_scope_unproved", operation_index=index)
            if action == "update":
                target = owned_resource(expected.get("resourceName"), client.customer_id, collection)
                if root == "campaignConversionGoal" and (campaign_name is None or target.split("/")[-1].split("~")[0] != campaign_name.rsplit("/", 1)[-1]):
                    raise _recovery_problem("google_ads_recovery_scope_mismatch", operation_index=index)
                where = _field(root, ("resourceName",)) + " = " + gaql_string(target)
            else:
                where = _recovery_scope(root, expected, campaign_name, correlation_label_name)
            checks, gaps, _ = _checks(root, expected, spec)
            await _metadata(client, _field_names(root, checks), metadata)
            resolved, selected, unresolved = _resolve(root, checks, metadata)
            if gaps or unresolved:
                raise _recovery_problem("google_ads_recovery_field_gap", operation_index=index, fields=sorted(set([*gaps, *unresolved]))[:40])
            candidates = await _read_one(client, root, collection, selected, where, metadata)
            matching = [name for name, value in candidates.items() if (action != "create" or name not in before) and not _matches(root, value, resolved, metadata)]
            if len(matching) != 1:
                raise _recovery_problem("google_ads_recovery_resource_missing" if not matching else "google_ads_recovery_resource_ambiguous", operation_index=index)
            name = matching[0]
            if name in [known for position, known in enumerate(names) if position != index and known is not None]:
                raise _recovery_problem("google_ads_recovery_resource_alias", operation_index=index)
            names[index] = name
            original = (spec.get("create") or {}).get("resourceName")
            if original:
                identities[original] = name
            pending.remove(index)
            progress = True
        if not progress:
            raise _recovery_problem("google_ads_recovery_dependency_incomplete", operation_indices=sorted(pending))
    return [str(name) for name in names]


def _has_temporary_reference(value, ignore_root_name=False):
    if isinstance(value, dict):
        return any(_has_temporary_reference(item) for key, item in value.items() if not (ignore_root_name and key == "resourceName"))
    if isinstance(value, list):
        return any(_has_temporary_reference(item) for item in value)
    return isinstance(value, str) and bool(re.fullmatch(r"customers/\d+/[A-Za-z]+/[^/]*-\d+[^/]*", value))


def _recovery_scope(root, expected, campaign_name, label_name):
    if root == "asset":
        name = expected.get("name")
        if not isinstance(name, str) or label_name not in name:
            raise _recovery_problem("google_ads_recovery_asset_correlation_missing")
        return "asset.name = " + gaql_string(name)
    parent_fields = {
        "adGroup": "campaign", "assetGroup": "campaign", "campaignCriterion": "campaign", "campaignAsset": "campaign",
        "adGroupAd": "adGroup", "adGroupCriterion": "adGroup", "adGroupAsset": "adGroup",
        "assetGroupAsset": "assetGroup", "assetGroupSignal": "assetGroup", "assetGroupListingGroupFilter": "assetGroup",
    }
    parent = parent_fields.get(root)
    if parent is None or not isinstance(expected.get(parent), str):
        raise _recovery_problem("google_ads_recovery_scope_unsupported", resource_type=snake(root))
    reference = expected[parent]
    if parent == "campaign" and reference != campaign_name:
        raise _recovery_problem("google_ads_recovery_scope_mismatch")
    return _field(root, (parent,)) + " = " + gaql_string(reference)


__all__ = ["verify_google_operations", "recover_create_operation_names"]
