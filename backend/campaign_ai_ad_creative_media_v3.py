"""Provider-safe actual ad creative media evidence for Decision Intelligence V3.

This collector is read-only. It resolves the provider creative attached to the
candidate and exposes only bounded, public HTTPS image/preview evidence. It does
not mutate ads or media. Missing or inaccessible media is represented explicitly
so the model can never infer that a video was watched when it was not.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from integrations_control_center.meta_oauth_security import (
    META_CREDENTIALS_COLLECTION,
    META_PROVIDER_ID,
    decrypt_meta_token,
    meta_appsecret_proof,
    meta_graph_base,
)
from integrations_control_center.snapchat_native_data_common import (
    SNAPCHAT_API_BASE,
    SNAPCHAT_ENTITY_COLLECTION,
    SnapchatSyncContext,
)

SCHEMA_VERSION = "campaign_ai_actual_ad_creative_media_v3"
MAX_CANDIDATES = 20
MAX_ADS_PER_CANDIDATE = 2
MAX_VISUALS_PER_CANDIDATE = 6
MAX_TOTAL_VISUALS = 12
HTTP_TIMEOUT_SECONDS = 12.0


def _entity_key(row: dict[str, Any]) -> str:
    return "|".join((
        str(row.get("provider") or ""),
        str(row.get("entity_level") or ""),
        str(row.get("account_id") or ""),
        str(row.get("entity_id") or ""),
    ))


def _public_https_url(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw or len(raw) > 4000:
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    host = parsed.hostname.casefold().rstrip(".")
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return None
    if not infos:
        return None
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            return None
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return None
    return raw


async def _validated_public_https_url(value: Any) -> str | None:
    return await asyncio.to_thread(_public_https_url, value)


def _extract_snap_media(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("media")
    if isinstance(rows, list):
        for row in rows[:3]:
            if not isinstance(row, dict):
                continue
            media = row.get("media") if isinstance(row.get("media"), dict) else row
            if isinstance(media, dict):
                return media
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


async def _find_snap_ads(db: Any, user_id: str, candidate: dict[str, Any]) -> list[dict[str, Any]]:
    level = str(candidate.get("entity_level") or "")
    entity_id = str(candidate.get("entity_id") or "")
    account_id = str(candidate.get("account_id") or "")
    query: dict[str, Any] = {
        "user_id": user_id,
        "entity_type": "ad",
    }
    if account_id:
        query["ad_account_id"] = account_id
    if level == "ad":
        query["external_id"] = entity_id
    elif level == "ad_group":
        query["ad_squad_id"] = entity_id
    elif level == "campaign":
        query["campaign_id"] = entity_id
    else:
        return []
    return await db[SNAPCHAT_ENTITY_COLLECTION].find(
        query,
        {"_id": 0},
    ).sort("updated_at", -1).limit(MAX_ADS_PER_CANDIDATE).to_list(length=MAX_ADS_PER_CANDIDATE)


async def _snap_candidate_media(
    db: Any,
    user_id: str,
    candidate: dict[str, Any],
    *,
    client: httpx.AsyncClient,
    context: SnapchatSyncContext,
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "provider": "snapchat",
        "entity_level": candidate.get("entity_level"),
        "entity_id": str(candidate.get("entity_id") or ""),
        "media_available": False,
        "visuals": [],
        "limitations": [],
    }
    ads = await _find_snap_ads(db, user_id, candidate)
    if not ads:
        output["limitations"].append("creative_media_unavailable:snap_ad_not_resolved")
        return output

    access_token = await context.access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    seen_media: set[str] = set()
    for ad in ads:
        creative_id = str(
            ad.get("creative_id")
            or ((ad.get("provider_snapshot") or {}).get("creative_id"))
            or ""
        ).strip()
        if not creative_id:
            output["limitations"].append("creative_media_unavailable:snap_creative_id_missing")
            continue
        creative = await db[SNAPCHAT_ENTITY_COLLECTION].find_one(
            {
                "user_id": user_id,
                "entity_type": "creative",
                "external_id": creative_id,
                **({"ad_account_id": ad.get("ad_account_id")} if ad.get("ad_account_id") else {}),
            },
            {"_id": 0},
        )
        snapshot = (creative or {}).get("provider_snapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else (creative or {})
        media_id = str(snapshot.get("top_snap_media_id") or "").strip()
        if not media_id or media_id in seen_media:
            if not media_id:
                output["limitations"].append("creative_media_unavailable:snap_top_snap_media_id_missing")
            continue
        seen_media.add(media_id)
        metadata_payload = await context.get_json(
            client,
            f"{SNAPCHAT_API_BASE}/media/{media_id}",
            headers=headers,
        )
        metadata = _extract_snap_media(metadata_payload)
        media_type = str(metadata.get("type") or "UNKNOWN").upper()
        item: dict[str, Any] = {
            "ad_id": str(ad.get("external_id") or ""),
            "creative_id": creative_id,
            "media_id": media_id,
            "media_type": media_type,
            "media_status": metadata.get("media_status"),
            "name": metadata.get("name"),
            "source": "snapchat_marketing_api_media",
            "inspection_scope": "provider_actual_media",
        }

        preview_payload = await context.get_json(
            client,
            f"{SNAPCHAT_API_BASE}/media/{media_id}/preview",
            headers=headers,
        )
        preview_link = await _validated_public_https_url(preview_payload.get("link"))
        if preview_link:
            item["preview_url"] = preview_link

        image_url = None
        if media_type == "VIDEO":
            try:
                thumbnail_payload = await context.get_json(
                    client,
                    f"{SNAPCHAT_API_BASE}/media/{media_id}/thumbnail",
                    headers=headers,
                )
                image_url = await _validated_public_https_url(thumbnail_payload.get("link"))
            except Exception as exc:  # provider may not expose a thumbnail for every asset
                output["limitations"].append(f"snap_thumbnail_unavailable:{type(exc).__name__}")
        elif media_type == "IMAGE":
            image_url = await _validated_public_https_url(
                metadata.get("download_link") or preview_payload.get("link")
            )

        if image_url:
            item["image_url"] = image_url
            item["visual_role"] = "actual_ad_video_thumbnail" if media_type == "VIDEO" else "actual_ad_image"
            output["visuals"].append(item)
        elif preview_link:
            output["limitations"].append("creative_media_preview_is_video_not_direct_image")
        if len(output["visuals"]) >= MAX_VISUALS_PER_CANDIDATE:
            break

    output["media_available"] = bool(output["visuals"])
    if not output["media_available"]:
        output["limitations"].append("creative_media_unavailable:no_safe_visual_asset")
    output["limitations"] = list(dict.fromkeys(output["limitations"]))
    return output


async def _meta_token(db: Any, user_id: str) -> str:
    row = await db[META_CREDENTIALS_COLLECTION].find_one(
        {"user_id": user_id, "provider": META_PROVIDER_ID},
        {"_id": 0, "access_token_ciphertext": 1, "access_token_expires_at": 1},
    )
    if not row:
        return ""
    expiry = row.get("access_token_expires_at")
    if isinstance(expiry, str):
        try:
            parsed = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if parsed.astimezone(timezone.utc) <= datetime.now(timezone.utc):
                return ""
    return decrypt_meta_token(row.get("access_token_ciphertext"))


def _meta_creative_fields() -> str:
    return (
        "id,name,thumbnail_url,image_url,image_hash,effective_object_story_id,"
        "object_story_spec,asset_feed_spec"
    )


async def _meta_get(client: httpx.AsyncClient, path: str, token: str, **params: Any) -> dict[str, Any]:
    response = await client.get(
        f"{meta_graph_base()}/{path.lstrip('/')}",
        params={
            **params,
            "access_token": token,
            "appsecret_proof": meta_appsecret_proof(token),
        },
    )
    response.raise_for_status()
    payload = response.json() or {}
    return payload if isinstance(payload, dict) else {}


def _video_ids_from_creative(creative: dict[str, Any]) -> list[str]:
    output: list[str] = []
    spec = creative.get("object_story_spec")
    spec = spec if isinstance(spec, dict) else {}
    video_data = spec.get("video_data")
    if isinstance(video_data, dict) and video_data.get("video_id"):
        output.append(str(video_data["video_id"]))
    feed = creative.get("asset_feed_spec")
    feed = feed if isinstance(feed, dict) else {}
    for video in (feed.get("videos") or [])[:10]:
        if isinstance(video, dict) and video.get("video_id"):
            output.append(str(video["video_id"]))
    return list(dict.fromkeys(output))[:3]


async def _meta_ads(client: httpx.AsyncClient, candidate: dict[str, Any], token: str) -> list[dict[str, Any]]:
    level = str(candidate.get("entity_level") or "")
    entity_id = str(candidate.get("entity_id") or "")
    fields = f"id,name,creative{{{_meta_creative_fields()}}}"
    if level == "ad":
        row = await _meta_get(client, entity_id, token, fields=fields)
        return [row] if row else []
    if level in {"campaign", "ad_group"}:
        payload = await _meta_get(
            client,
            f"{entity_id}/ads",
            token,
            fields=fields,
            limit=MAX_ADS_PER_CANDIDATE,
        )
        rows = payload.get("data")
        return [row for row in (rows or []) if isinstance(row, dict)][:MAX_ADS_PER_CANDIDATE]
    return []


async def _meta_candidate_media(
    candidate: dict[str, Any],
    *,
    client: httpx.AsyncClient,
    token: str,
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "provider": "meta",
        "entity_level": candidate.get("entity_level"),
        "entity_id": str(candidate.get("entity_id") or ""),
        "media_available": False,
        "visuals": [],
        "limitations": [],
    }
    if not token:
        output["limitations"].append("creative_media_unavailable:meta_credential_missing_or_expired")
        return output
    try:
        ads = await _meta_ads(client, candidate, token)
    except Exception as exc:
        output["limitations"].append(f"creative_media_unavailable:meta_ad_lookup:{type(exc).__name__}")
        return output
    if not ads:
        output["limitations"].append("creative_media_unavailable:meta_ad_not_resolved")
        return output

    seen: set[str] = set()
    for ad in ads:
        creative = ad.get("creative")
        creative = creative if isinstance(creative, dict) else {}
        creative_id = str(creative.get("id") or "")
        for role, raw_url in (
            ("actual_ad_thumbnail", creative.get("thumbnail_url")),
            ("actual_ad_image", creative.get("image_url")),
        ):
            url = await _validated_public_https_url(raw_url)
            if not url or url in seen:
                continue
            seen.add(url)
            output["visuals"].append({
                "ad_id": str(ad.get("id") or ""),
                "creative_id": creative_id,
                "image_url": url,
                "visual_role": role,
                "source": "meta_graph_ad_creative",
                "inspection_scope": "provider_actual_media",
            })

        for video_id in _video_ids_from_creative(creative):
            try:
                video = await _meta_get(
                    client,
                    video_id,
                    token,
                    fields="id,picture,thumbnails",
                )
            except Exception as exc:
                output["limitations"].append(f"meta_video_thumbnail_unavailable:{type(exc).__name__}")
                continue
            candidates: list[Any] = [video.get("picture")]
            thumbs = video.get("thumbnails")
            thumbs = thumbs.get("data") if isinstance(thumbs, dict) else []
            for thumb in (thumbs or [])[:6]:
                if isinstance(thumb, dict):
                    candidates.append(thumb.get("uri"))
            for raw_url in candidates:
                url = await _validated_public_https_url(raw_url)
                if not url or url in seen:
                    continue
                seen.add(url)
                output["visuals"].append({
                    "ad_id": str(ad.get("id") or ""),
                    "creative_id": creative_id,
                    "video_id": video_id,
                    "image_url": url,
                    "visual_role": "actual_ad_video_provider_frame",
                    "source": "meta_graph_video_thumbnail",
                    "inspection_scope": "provider_actual_media",
                })
                if len(output["visuals"]) >= MAX_VISUALS_PER_CANDIDATE:
                    break
            if len(output["visuals"]) >= MAX_VISUALS_PER_CANDIDATE:
                break
        if len(output["visuals"]) >= MAX_VISUALS_PER_CANDIDATE:
            break

    output["media_available"] = bool(output["visuals"])
    if not output["media_available"]:
        output["limitations"].append("creative_media_unavailable:no_safe_visual_asset")
    output["limitations"] = list(dict.fromkeys(output["limitations"]))
    return output


async def build_actual_ad_creative_media_evidence(
    db: Any,
    user_id: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve bounded actual creative visuals for the highest-spend candidates."""
    ordered = sorted(
        candidates,
        key=lambda row: float(row.get("spend_sar") or 0),
        reverse=True,
    )[:MAX_CANDIDATES]
    entities: dict[str, Any] = {}
    limitations: list[str] = []
    total_visuals = 0
    snap_context = SnapchatSyncContext(db=db, user_id=user_id)
    meta_token: str | None = None

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS),
        follow_redirects=False,
        headers={"User-Agent": "MezanCampaignCreativeEvidence/3.0"},
    ) as client:
        for candidate in ordered:
            key = _entity_key(candidate)
            provider = str(candidate.get("provider") or "").lower()
            if total_visuals >= MAX_TOTAL_VISUALS:
                entities[key] = {
                    "provider": provider,
                    "entity_level": candidate.get("entity_level"),
                    "entity_id": str(candidate.get("entity_id") or ""),
                    "media_available": False,
                    "visuals": [],
                    "limitations": ["creative_media_not_collected_due_to_global_bound"],
                }
                continue
            try:
                if provider == "snapchat":
                    block = await _snap_candidate_media(
                        db,
                        user_id,
                        candidate,
                        client=client,
                        context=snap_context,
                    )
                elif provider == "meta":
                    if meta_token is None:
                        meta_token = await _meta_token(db, user_id)
                    block = await _meta_candidate_media(candidate, client=client, token=meta_token)
                else:
                    block = {
                        "provider": provider,
                        "entity_level": candidate.get("entity_level"),
                        "entity_id": str(candidate.get("entity_id") or ""),
                        "media_available": False,
                        "visuals": [],
                        "limitations": ["creative_media_unavailable:provider_not_supported_v3"],
                    }
            except Exception as exc:
                block = {
                    "provider": provider,
                    "entity_level": candidate.get("entity_level"),
                    "entity_id": str(candidate.get("entity_id") or ""),
                    "media_available": False,
                    "visuals": [],
                    "limitations": [f"creative_media_unavailable:{type(exc).__name__}"],
                }
            remaining = max(0, MAX_TOTAL_VISUALS - total_visuals)
            block["visuals"] = list(block.get("visuals") or [])[:remaining]
            block["media_available"] = bool(block["visuals"])
            total_visuals += len(block["visuals"])
            entities[key] = block
            limitations.extend(block.get("limitations") or [])

    return {
        "schema_version": SCHEMA_VERSION,
        "entities": entities,
        "visual_count": total_visuals,
        "candidate_limit": MAX_CANDIDATES,
        "visual_limit": MAX_TOTAL_VISUALS,
        "contract": {
            "provider_media_read_only": True,
            "raw_video_claimed_watched_without_frames": False,
            "safe_public_https_only": True,
            "missing_media_is_explicit": True,
            "openai_remains_final_marketing_decision_authority": True,
        },
        "limitations": list(dict.fromkeys(limitations)),
    }


__all__ = [
    "MAX_TOTAL_VISUALS",
    "SCHEMA_VERSION",
    "build_actual_ad_creative_media_evidence",
]
