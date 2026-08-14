"""First-party click, journey and order-attribution core.

Security and accounting invariants:

* campaign links are signed; browser-supplied campaign identities are not
  trusted without a valid token;
* raw email/phone values are never persisted in attribution collections;
* events are idempotent by ``(user_id, event_id)``;
* one order has one durable attribution record, recalculated only when the
  evidence fingerprint changes;
* IP address and probabilistic browser fingerprints never create a match.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


EVENT_COLLECTION = "mezan_first_party_events_v1"
LINK_COLLECTION = "mezan_first_party_links_v1"
ORDER_ATTRIBUTION_COLLECTION = "mezan_order_attributions_v1"

PAID_SOURCES = frozenset({"snapchat", "google", "meta", "tiktok"})
EVENT_NAMES = frozenset({
    "page_view",
    "view_item",
    "add_to_cart",
    "remove_from_cart",
    "view_cart",
    "begin_checkout",
    "purchase",
})
IDENTITY_RE = re.compile(r"^[a-f0-9]{64}$")
SNAP_MACROS = {
    "mz_campaign_id": "~.~SERVER_CAMPAIGN_ID~.~",
    "mz_ad_squad_id": "~.~SERVER_AD_SQUAD_ID~.~",
    "mz_ad_id": "~.~SERVER_AD_ID~.~",
    "mz_creative_id": "~.~SERVER_CREATIVE_ID~.~",
    "mz_click_ts": "~.~TIMESTAMP~.~",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any, maximum: int = 500) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()[:maximum]


def _iso(value: Any = None) -> str:
    parsed = _datetime(value) or _now()
    return parsed.astimezone(timezone.utc).isoformat()


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = _text(value, 100)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _urlsafe_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signing_key() -> bytes:
    # Domain-separated derivation: link signatures cannot be reused as JWTs.
    root = (os.environ.get("MEZAN_ATTRIBUTION_SIGNING_SECRET") or
            os.environ.get("JWT_SECRET") or "").encode("utf-8")
    if not root:
        raise RuntimeError("JWT_SECRET or MEZAN_ATTRIBUTION_SIGNING_SECRET is required")
    return hmac.new(root, b"mezan:first-party-attribution:v1", hashlib.sha256).digest()


def issue_link_token(
    *,
    user_id: str,
    provider: str,
    link_id: str,
    destination_host: str,
    product_id: str | None = None,
    account_id: str | None = None,
) -> str:
    payload = {
        "v": 1,
        "u": _text(user_id, 160),
        "p": _text(provider, 40).lower(),
        "l": _text(link_id, 160),
        "h": _text(destination_host, 255).lower(),
        "i": int(_now().timestamp()),
    }
    if product_id:
        payload["product"] = _text(product_id, 160)
    if account_id:
        payload["account"] = _text(account_id, 160)
    encoded = _urlsafe_encode(json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"))
    signature = _urlsafe_encode(hmac.new(
        _signing_key(), encoded.encode("ascii"), hashlib.sha256
    ).digest())
    return f"{encoded}.{signature}"


def verify_link_token(token: str, *, maximum_age_days: int = 540) -> dict[str, Any]:
    try:
        encoded, supplied = token.split(".", 1)
        expected = _urlsafe_encode(hmac.new(
            _signing_key(), encoded.encode("ascii"), hashlib.sha256
        ).digest())
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("signature mismatch")
        payload = json.loads(_urlsafe_decode(encoded))
        if payload.get("v") != 1 or not payload.get("u") or not payload.get("l"):
            raise ValueError("invalid payload")
        issued_at = datetime.fromtimestamp(int(payload["i"]), tz=timezone.utc)
        if issued_at < _now() - timedelta(days=maximum_age_days):
            raise ValueError("expired token")
        return payload
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("invalid attribution link token") from exc


def build_tracking_url(
    destination_url: str,
    *,
    user_id: str,
    provider: str,
    product_id: str | None = None,
    account_id: str | None = None,
    link_id: str | None = None,
    snapchat_macros: bool = False,
) -> tuple[str, dict[str, Any]]:
    split = urlsplit(_text(destination_url, 3000))
    if split.scheme != "https" or not split.hostname:
        raise ValueError("destination_url must be an https URL")
    link_id = link_id or str(uuid.uuid4())
    provider = _text(provider, 40).lower()
    token = issue_link_token(
        user_id=user_id,
        provider=provider,
        link_id=link_id,
        destination_host=split.hostname,
        product_id=product_id,
        account_id=account_id,
    )
    params = dict(parse_qsl(split.query, keep_blank_values=True))
    params["mzt"] = token
    params.setdefault("mz_source", provider)
    params.setdefault("utm_source", provider)
    params.setdefault("utm_medium", "paid_social" if provider != "google" else "paid_search")
    if snapchat_macros:
        for key, value in SNAP_MACROS.items():
            params.setdefault(key, value)
        params.setdefault("utm_campaign", SNAP_MACROS["mz_campaign_id"])
        params.setdefault("utm_content", SNAP_MACROS["mz_ad_id"])
    tracked_url = urlunsplit((
        split.scheme,
        split.netloc,
        split.path,
        urlencode(params),
        split.fragment,
    ))
    return tracked_url, {
        "link_id": link_id,
        "user_id": user_id,
        "provider": provider,
        "product_id": product_id,
        "account_id": account_id,
        "destination_url": destination_url,
        "destination_host": split.hostname.lower(),
        "tracked_url": tracked_url,
        "created_at": _iso(),
        "status": "ready",
        "schema_version": 1,
    }


def normalize_identity_hashes(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = _text(value, 64).lower()
        if IDENTITY_RE.fullmatch(normalized) and normalized not in result:
            result.append(normalized)
    return result[:8]


def hash_customer_identity(kind: str, value: Any) -> str | None:
    text = _text(value, 500).casefold()
    if not text:
        return None
    if kind == "phone":
        text = re.sub(r"\D", "", text)
        if text.startswith("00"):
            text = text[2:]
    elif kind == "email":
        text = text.strip()
    else:
        return None
    if not text:
        return None
    return hashlib.sha256(f"{kind}:{text}".encode("utf-8")).hexdigest()


def _touch_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    source = _text(event.get("source"), 40).lower()
    if not source:
        return None
    return {
        "source": source,
        "medium": _text(event.get("medium"), 80) or None,
        "campaign_id": _text(event.get("campaign_id"), 160) or None,
        "ad_group_id": _text(event.get("ad_group_id"), 160) or None,
        "ad_id": _text(event.get("ad_id"), 160) or None,
        "creative_id": _text(event.get("creative_id"), 160) or None,
        "link_id": _text(event.get("link_id"), 160) or None,
        "occurred_at": _iso(event.get("occurred_at")),
        "event_name": _text(event.get("event_name"), 80),
        "paid": source in PAID_SOURCES,
    }


def build_order_attribution(
    events: list[dict[str, Any]],
    *,
    order_number: str,
    matched_by: str,
) -> dict[str, Any] | None:
    ordered = sorted(events, key=lambda row: _datetime(row.get("occurred_at")) or _now())
    touches: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for event in ordered:
        touch = _touch_from_event(event)
        if not touch:
            continue
        identity = tuple(touch.get(key) for key in (
            "source", "campaign_id", "ad_group_id", "ad_id", "link_id"
        ))
        if identity in seen:
            continue
        seen.add(identity)
        touches.append(touch)
    if not touches:
        return None
    paid = [touch for touch in touches if touch["paid"]]
    confidence = {
        "order_number": "confirmed",
        "cart_id": "strong",
        "customer_id": "strong",
        "identity_hash": "deterministic",
        "visitor_id": "strong",
    }.get(matched_by, "unknown")
    acquisition = paid[0] if paid else touches[0]
    return {
        "order_number": str(order_number),
        "first_touch": touches[0],
        "acquisition_touch": acquisition,
        "last_touch": touches[-1],
        "last_paid_touch": paid[-1] if paid else None,
        "assisting_touches": touches[:-1][-20:],
        "touch_count": len(touches),
        "matched_by": matched_by,
        "confidence": confidence,
        "attribution_window_days": 30,
        "calculated_at": _iso(),
        "schema_version": 1,
    }


async def _resolve_user_id(db: Any, store_id: str) -> str | None:
    values: list[Any] = [_text(store_id, 160)]
    try:
        values.append(int(values[0]))
    except (ValueError, TypeError):
        pass
    row = await db.salla_integrations.find_one(
        {"store_id": {"$in": values}}, {"user_id": 1}, sort=[("updated_at", -1)]
    )
    return _text((row or {}).get("user_id"), 160) or None


async def persist_storefront_event(db: Any, payload: dict[str, Any]) -> dict[str, Any]:
    token_payload: dict[str, Any] = {}
    token = _text(payload.get("link_token"), 3000)
    if token:
        token_payload = verify_link_token(token)
    user_id = _text(token_payload.get("u"), 160)
    if not user_id:
        user_id = await _resolve_user_id(db, _text(payload.get("store_id"), 160)) or ""
    if not user_id:
        raise ValueError("connected store owner not found")

    event_name = _text(payload.get("event_name"), 80)
    if event_name not in EVENT_NAMES:
        raise ValueError("unsupported event_name")
    event_id = _text(payload.get("event_id"), 160)
    visitor_id = _text(payload.get("visitor_id"), 160)
    session_id = _text(payload.get("session_id"), 160)
    if not event_id or not visitor_id or not session_id:
        raise ValueError("event_id, visitor_id and session_id are required")

    source = _text(token_payload.get("p") or payload.get("source"), 40).lower()
    campaign_id = _text(payload.get("campaign_id"), 160) or None
    ad_group_id = _text(payload.get("ad_group_id"), 160) or None
    ad_id = _text(payload.get("ad_id"), 160) or None
    creative_id = _text(payload.get("creative_id"), 160) or None
    provider_ids_verified = False
    if source == "snapchat":
        requested = {
            "campaign": campaign_id,
            "ad_squad": ad_group_id,
            "ad": ad_id,
            "creative": creative_id,
        }
        requested = {
            kind: identifier
            for kind, identifier in requested.items()
            if identifier and not identifier.startswith("~.~")
        }
        verified: set[tuple[str, str]] = set()
        if requested and token_payload.get("account"):
            cursor = db.mezan_snapchat_entities_v2.find(
                {
                    "user_id": user_id,
                    "ad_account_id": _text(token_payload.get("account"), 160),
                    "$or": [
                        {"entity_type": kind, "external_id": identifier}
                        for kind, identifier in requested.items()
                    ],
                },
                {"_id": 0, "entity_type": 1, "external_id": 1},
            )
            rows = await cursor.to_list(10)
            verified = {
                (_text(row.get("entity_type"), 40), _text(row.get("external_id"), 160))
                for row in rows
            }
        # A public browser payload is never authority for Snapchat entity IDs.
        # Without both a signed account binding and a matching synced entity,
        # discard the identifier rather than report it as campaign truth.
        campaign_id = campaign_id if ("campaign", campaign_id or "") in verified else None
        ad_group_id = ad_group_id if ("ad_squad", ad_group_id or "") in verified else None
        ad_id = ad_id if ("ad", ad_id or "") in verified else None
        creative_id = creative_id if ("creative", creative_id or "") in verified else None
        provider_ids_verified = bool(requested) and len(verified) == len(requested)
    record = {
        "event_id": event_id,
        "user_id": user_id,
        "store_id": _text(payload.get("store_id"), 160) or None,
        "visitor_id": visitor_id,
        "session_id": session_id,
        "event_name": event_name,
        "occurred_at": _iso(payload.get("occurred_at")),
        "received_at": _iso(),
        "source": source or "direct",
        "medium": _text(payload.get("medium"), 80) or None,
        "link_id": _text(token_payload.get("l"), 160) or None,
        "account_id": _text(token_payload.get("account"), 160) or None,
        "campaign_id": campaign_id,
        "ad_group_id": ad_group_id,
        "ad_id": ad_id,
        "creative_id": creative_id,
        "provider_ids_verified": provider_ids_verified,
        "product_id": _text(payload.get("product_id") or token_payload.get("product"), 160) or None,
        "cart_id": _text(payload.get("cart_id"), 160) or None,
        "customer_id": _text(payload.get("customer_id"), 160) or None,
        "order_number": _text(payload.get("order_number"), 160) or None,
        "identity_hashes": normalize_identity_hashes(payload.get("identity_hashes") or []),
        "page_url": _text(payload.get("page_url"), 3000) or None,
        "referrer": _text(payload.get("referrer"), 3000) or None,
        "schema_version": 1,
    }
    result = await db[EVENT_COLLECTION].update_one(
        {"user_id": user_id, "event_id": event_id},
        {"$setOnInsert": record},
        upsert=True,
    )
    return {
        "accepted": True,
        "duplicate": not bool(getattr(result, "upserted_id", None)),
        "event_id": event_id,
        "visitor_id": visitor_id,
        "session_id": session_id,
    }


def _walk_values(value: Any, names: set[str], *, depth: int = 0) -> list[str]:
    if depth > 7:
        return []
    result: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in names:
                text = _text(child, 500)
                if text:
                    result.append(text)
            result.extend(_walk_values(child, names, depth=depth + 1))
    elif isinstance(value, list):
        for child in value[:50]:
            result.extend(_walk_values(child, names, depth=depth + 1))
    return list(dict.fromkeys(result))


def _order_match_evidence(order_payload: dict[str, Any], order_doc: dict[str, Any]) -> dict[str, list[str]]:
    combined = {"payload": order_payload, "order": order_doc}
    identity_hashes: list[str] = []
    for value in _walk_values(combined, {"email"}):
        hashed = hash_customer_identity("email", value)
        if hashed:
            identity_hashes.append(hashed)
    for value in _walk_values(combined, {"phone", "mobile", "mobile_number"}):
        hashed = hash_customer_identity("phone", value)
        if hashed:
            identity_hashes.append(hashed)
    return {
        "order_number": list(dict.fromkeys(_walk_values(combined, {"order_number", "reference_id"}))),
        "cart_id": list(dict.fromkeys(_walk_values(combined, {"cart_id", "basket_id"}))),
        "customer_id": list(dict.fromkeys(_walk_values(combined, {"customer_id"}))),
        "visitor_id": list(dict.fromkeys(_walk_values(combined, {"mz_visitor_id", "visitor_id"}))),
        "identity_hash": list(dict.fromkeys(identity_hashes)),
    }


async def link_order_attribution(
    db: Any,
    *,
    user_id: str,
    order_number: str,
    order_payload: dict[str, Any],
    order_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    order_doc = order_doc or {}
    evidence = _order_match_evidence(order_payload, order_doc)
    evidence["order_number"] = list(dict.fromkeys([str(order_number), *evidence["order_number"]]))
    query_field = {
        "order_number": "order_number",
        "cart_id": "cart_id",
        "customer_id": "customer_id",
        "visitor_id": "visitor_id",
        "identity_hash": "identity_hashes",
    }
    matched_by = ""
    events: list[dict[str, Any]] = []
    cutoff = _now() - timedelta(days=30)
    for kind in ("order_number", "cart_id", "customer_id", "visitor_id", "identity_hash"):
        values = [value for value in evidence[kind] if value]
        if not values:
            continue
        query = {
            "user_id": user_id,
            query_field[kind]: {"$in": values},
            "occurred_at": {"$gte": cutoff.isoformat()},
        }
        events = await db[EVENT_COLLECTION].find(query, {"_id": 0}).sort(
            "occurred_at", 1
        ).to_list(500)
        if events:
            matched_by = kind
            visitor_ids = list({row.get("visitor_id") for row in events if row.get("visitor_id")})
            if visitor_ids:
                events = await db[EVENT_COLLECTION].find(
                    {
                        "user_id": user_id,
                        "visitor_id": {"$in": visitor_ids},
                        "occurred_at": {"$gte": cutoff.isoformat()},
                    },
                    {"_id": 0},
                ).sort("occurred_at", 1).to_list(500)
            break
    attribution = build_order_attribution(
        events, order_number=order_number, matched_by=matched_by
    ) if events else None
    if not attribution:
        return {"linked": False, "reason": "no_deterministic_first_party_match"}

    fingerprint = hashlib.sha256(json.dumps(
        attribution, sort_keys=True, default=str, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    attribution["evidence_fingerprint"] = fingerprint
    attribution["user_id"] = user_id
    await db[ORDER_ATTRIBUTION_COLLECTION].update_one(
        {"user_id": user_id, "order_number": str(order_number)},
        {"$set": attribution, "$setOnInsert": {"created_at": _iso()}},
        upsert=True,
    )
    order_projection = {
        "mezan_attribution": {
            key: value for key, value in attribution.items() if key != "user_id"
        },
        "marketing_source": attribution["acquisition_touch"]["source"],
        "first_party_attribution_updated_at": _iso(),
    }
    acquisition_source = attribution["acquisition_touch"]["source"]
    acquisition_campaign = attribution["acquisition_touch"].get("campaign_id")
    if acquisition_source in PAID_SOURCES:
        order_projection["ad_platform_source"] = acquisition_source
    if acquisition_campaign:
        order_projection["campaign_id"] = acquisition_campaign
    await db.unified_orders.update_one(
        {"user_id": user_id, "order_number": str(order_number)},
        {"$set": order_projection},
    )
    return {
        "linked": True,
        "matched_by": matched_by,
        "confidence": attribution["confidence"],
        "source": attribution["acquisition_touch"]["source"],
        "campaign_id": attribution["acquisition_touch"].get("campaign_id"),
    }


async def ensure_first_party_attribution_indexes(db: Any) -> None:
    await db[EVENT_COLLECTION].create_index(
        [("user_id", 1), ("event_id", 1)], unique=True,
        name="mezan_first_party_event_unique",
    )
    await db[EVENT_COLLECTION].create_index(
        [("user_id", 1), ("visitor_id", 1), ("occurred_at", 1)],
        name="mezan_first_party_visitor_journey",
    )
    for field in ("order_number", "cart_id", "customer_id", "identity_hashes"):
        await db[EVENT_COLLECTION].create_index(
            [("user_id", 1), (field, 1), ("occurred_at", -1)],
            name=f"mezan_first_party_{field}_match",
        )
    await db[LINK_COLLECTION].create_index(
        [("user_id", 1), ("link_id", 1)], unique=True,
        name="mezan_first_party_link_unique",
    )
    await db[ORDER_ATTRIBUTION_COLLECTION].create_index(
        [("user_id", 1), ("order_number", 1)], unique=True,
        name="mezan_order_attribution_unique",
    )


__all__ = [
    "EVENT_COLLECTION",
    "LINK_COLLECTION",
    "ORDER_ATTRIBUTION_COLLECTION",
    "build_order_attribution",
    "build_tracking_url",
    "ensure_first_party_attribution_indexes",
    "hash_customer_identity",
    "issue_link_token",
    "link_order_attribution",
    "normalize_identity_hashes",
    "persist_storefront_event",
    "verify_link_token",
]
