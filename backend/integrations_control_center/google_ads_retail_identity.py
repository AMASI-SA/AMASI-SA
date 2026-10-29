"""Fail-closed native offer selection, not proof of a store-to-feed mapping.

The caller owns a fresh, complete account-scoped shopping_product read. Item IDs
alone do not identify a native product across feeds, languages or channels.
No IO, provider validation, campaign approval or launch authority lives here.
"""
from __future__ import annotations

import re

from .google_ads_client import MAX_ROWS, problem


def selected_native_products(inventory, *, account_id, merchant_id, offer_ids, feed_label=None, require_feed=True):
    """Resolve only unique native products; preserve ineligible collision rows.

    Even a fixed feed is conservatively rejected when a selected item ID occurs
    elsewhere in this merchant inventory: existing UI selection carries no full
    native identity. Multi-client merchant inheritance is not inferred.
    """
    if (not isinstance(inventory, dict) or inventory.get("complete") is not True
        or inventory.get("source") != "google_ads_api" or not isinstance(inventory.get("items"), list)
        or len(inventory["items"]) > MAX_ROWS or inventory.get("field_gaps")):
        raise problem("google_ads_product_inventory_incomplete", "تعذر إثبات اكتمال هوية منتجات جوجل؛ حدّث قائمة المنتجات.", 409)
    if require_feed and (not isinstance(feed_label, str) or not re.fullmatch(r"[A-Z0-9_-]{1,20}", feed_label)):
        raise problem("google_ads_product_feed_scope_unproved", "اختر تغذية المنتجات المحددة قبل معاينة الحملة.", 422)
    by_offer = {}
    seen = set()
    for row in inventory["items"]:
        if not isinstance(row, dict):
            raise problem("google_ads_product_identity_unproved", "هوية منتج جوجل غير مكتملة.", 409)
        offer, language, feed, channel = (row.get(key) for key in ("offer_id", "language_code", "feed_label", "channel"))
        resource = row.get("resource_name")
        if (row.get("merchant_id") != merchant_id or not isinstance(offer, str) or not 1 <= len(offer) <= 150
            or any(ord(char) < 32 for char in offer) or not isinstance(language, str)
            or not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", language)
            or not isinstance(feed, str) or not re.fullmatch(r"[A-Z0-9_-]{1,20}", feed)
            or channel not in ("ONLINE", "LOCAL") or not isinstance(resource, str)):
            raise problem("google_ads_product_identity_unproved", "حساب المنتج أو تغذيته أو لغته غير مثبتة.", 409)
        prefix = f"customers/{account_id}/shoppingProducts/{merchant_id}~"
        parts = resource[len(prefix):].split("~", 3) if resource.startswith(prefix) else []
        # Both named and numeric enum representations must agree with the read
        # channel; the remaining identity components are compared literally.
        channel_values = {channel, "2" if channel == "ONLINE" else "3"}
        if len(parts) != 4 or parts[0] not in channel_values or parts[1:] != [language, feed, offer] or resource in seen:
            raise problem("google_ads_product_identity_unproved", "هوية منتج جوجل متعارضة أو مكررة؛ أعد قراءة التغذية.", 409)
        seen.add(resource)
        by_offer.setdefault(offer, []).append(row)
    result = []
    for offer in sorted(set(offer_ids)):
        rows = by_offer.get(offer, [])
        if len(rows) != 1:
            raise problem("google_ads_product_identity_ambiguous", "معرّف المنتج غير موجود أو يتكرر بين تغذيات أو لغات؛ لا يمكن اعتماد هذا الاختيار.", 422)
        row = rows[0]
        if feed_label is not None and row["feed_label"] != feed_label:
            raise problem("google_ads_product_feed_mismatch", "منتج جوجل المختار لا ينتمي إلى تغذية الحملة.", 422)
        # PREORDER needs a separate fulfilment contract; absent/unknown enums
        # are not stock evidence. Status is account-scope, not delivery proof.
        if (row.get("status") not in ("ELIGIBLE", "ELIGIBLE_LIMITED")
            or row.get("availability") != "IN_STOCK" or row["channel"] != "ONLINE"):
            raise problem("google_ads_product_offer_not_eligible", "لم تثبت أهلية المنتج وتوفره للبيع عبر الإنترنت.", 422)
        result.append({key: row[key] for key in ("offer_id", "merchant_id", "resource_name", "channel", "language_code", "feed_label", "status", "availability")}
                        | {"name": row.get("title") or row.get("name") or "منتج جوجل",
                           "eligibility_scope": "account_inventory", "mapping_status": "owner_selected_unverified"})
    return result
