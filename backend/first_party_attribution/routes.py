"""HTTP surface for Mezan first-party attribution."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .core import LINK_COLLECTION, build_tracking_url, persist_storefront_event


class StorefrontEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=8, max_length=160)
    visitor_id: str = Field(min_length=8, max_length=160)
    session_id: str = Field(min_length=8, max_length=160)
    event_name: str = Field(min_length=3, max_length=80)
    occurred_at: str | None = Field(default=None, max_length=100)
    store_id: str | None = Field(default=None, max_length=160)
    link_token: str | None = Field(default=None, max_length=3000)
    source: str | None = Field(default=None, max_length=40)
    medium: str | None = Field(default=None, max_length=80)
    campaign_id: str | None = Field(default=None, max_length=160)
    ad_group_id: str | None = Field(default=None, max_length=160)
    ad_id: str | None = Field(default=None, max_length=160)
    creative_id: str | None = Field(default=None, max_length=160)
    product_id: str | None = Field(default=None, max_length=160)
    cart_id: str | None = Field(default=None, max_length=160)
    customer_id: str | None = Field(default=None, max_length=160)
    order_number: str | None = Field(default=None, max_length=160)
    identity_hashes: list[str] = Field(default_factory=list, max_length=8)
    page_url: str | None = Field(default=None, max_length=3000)
    referrer: str | None = Field(default=None, max_length=3000)


class TrackingLinkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_url: str = Field(min_length=10, max_length=3000)
    provider: str = Field(min_length=2, max_length=40)
    product_id: str | None = Field(default=None, max_length=160)
    account_id: str | None = Field(default=None, max_length=160)
    use_snapchat_macros: bool = False

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"snapchat", "google", "meta", "tiktok"}:
            raise ValueError("unsupported provider")
        return normalized


def make_first_party_attribution_router(
    db: Any,
    get_current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(prefix="/first-party-attribution/v1", tags=["attribution"])

    @router.post("/events")
    async def capture_event(payload: StorefrontEventInput, request: Request):
        # The signed token/store binding is the authority. Origin is retained
        # only for audit and is never used as customer identity evidence.
        event = payload.model_dump(mode="json")
        event["request_origin"] = request.headers.get("origin")
        try:
            return await persist_storefront_event(db, event)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "first_party_event_rejected", "message": str(exc)},
            ) from exc

    @router.post("/links")
    async def create_link(
        payload: TrackingLinkInput,
        user: dict = Depends(get_current_user),
    ):
        try:
            tracked_url, record = build_tracking_url(
                payload.destination_url,
                user_id=str(user["id"]),
                provider=payload.provider,
                product_id=payload.product_id,
                account_id=payload.account_id,
                snapchat_macros=payload.use_snapchat_macros,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "tracking_link_invalid", "message": str(exc)},
            ) from exc
        await db[LINK_COLLECTION].update_one(
            {"user_id": record["user_id"], "link_id": record["link_id"]},
            {"$setOnInsert": record},
            upsert=True,
        )
        return {"tracked_url": tracked_url, "link_id": record["link_id"]}

    return router


__all__ = ["StorefrontEventInput", "TrackingLinkInput", "make_first_party_attribution_router"]
