"""Signed read-only Salla catalog for the Amasi WhatsApp sales assistant.

Always reads authenticated Salla Merchant API via Mezan's existing tenant
integration. Public-only projection excludes costs, supplier data, customer
data and payment details. No order creation or Salla mutations.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request

from salla_integration.service import call_salla
from .whatsapp_stage_service_auth import (
    verify_stage_service_request, StageServiceUnauthorized, CATALOG_PATH
)
from .whatsapp_salla_catalog_projection import project, InvalidProduct

MAX_BODY=2048


def _provider_payload(response, *, list_expected):
    if not isinstance(response, dict) or response.get("success") is False:
        raise ValueError("untrusted_salla_response")
    data=response.get("data")
    if list_expected:
        if not isinstance(data,list) or len(data)>20:
            raise ValueError("untrusted_salla_products")
    elif not isinstance(data,dict):
        raise ValueError("untrusted_salla_product")
    return data


def _url_from(value):
    if isinstance(value,str):
        return value
    if isinstance(value,dict):
        return value.get("url") or value.get("original") or value.get("src")
    return None


def _shape_salla_product(row):
    """Normalize only documented public Salla fields, never invent options."""
    if not isinstance(row,dict):
        raise InvalidProduct("untrusted_product_source")
    raw=dict(row)
    urls=raw.get("urls")
    if isinstance(urls,dict):
        raw["url"]=urls.get("customer")
    if not raw.get("url"):
        raise InvalidProduct("untrusted_product_url")
    image=_url_from(raw.get("main_image") or raw.get("thumbnail"))
    if image:
        raw["main_image"]=image
    image_rows=raw.get("images")
    if image_rows is None:
        image_rows=[]
    if not isinstance(image_rows,list):
        raise InvalidProduct("untrusted_product_images")
    raw["images"]=[
        {"url":_url_from(image)} for image in image_rows
        if _url_from(image)
    ]
    if "unlimited_quantity" not in raw or type(raw["unlimited_quantity"]) is not bool:
        raw["unlimited_quantity"]=False
    if type(raw.get("quantity")) is not int:
        raw["quantity"]=None
    return raw


def public_salla_product(row, *, fetched_at, detailed):
    """With unrecognized options return product card but never claim options."""
    raw=_shape_salla_product(row)
    options=raw.get("options")
    has_options=isinstance(options,list) and len(options)>0
    # The minimal search listing cannot establish any personalization facts.
    if not detailed or not isinstance(options,list):
        raw["options"]=[]
        options_verified=bool(detailed and isinstance(options,list))
    else:
        options_verified=True
    try:
        facts=project(raw, fetched_at=fetched_at)
    except InvalidProduct:
        if not detailed or not has_options:
            raise
        # Conservative fallback for newly introduced Salla option variants:
        # preserve public product facts, explicitly remove option assertions.
        raw["options"]=[]
        facts=project(raw, fetched_at=fetched_at)
        options_verified=False
    if facts["list_status"]!="sale" or facts["inventory_hint"]=="out":
        raise InvalidProduct("not_publicly_purchasable")
    facts["options_verified"]=options_verified
    # Exact source time; no cached indefinite item links or prices.
    facts["source"]="verified_salla_mezan_read_v1"
    return facts


def make_whatsapp_catalog_router(*, db: Any, fixed_owner_id: str,
                                 active_keys, claim_nonce, now_fn=None):
    import json
    import re
    import time
    now_fn=now_fn or (lambda:int(time.time()))
    if not fixed_owner_id or not active_keys or not callable(claim_nonce):
        raise ValueError("catalog_unconfigured")
    router=APIRouter(prefix="/internal/whatsapp/catalog",tags=["internal-whatsapp"])

    @router.post("/read")
    async def read_catalog(request: Request):
        try:
            body=await request.body()
            if len(body)>MAX_BODY:
                raise ValueError("oversize")
            await asyncio.to_thread(
                verify_stage_service_request,
                method=request.method,path=request.url.path,body=body,
                timestamp=request.headers.get("x-amasi-timestamp",""),
                nonce=request.headers.get("x-amasi-nonce",""),
                key_id=request.headers.get("x-amasi-key-id",""),
                signature=request.headers.get("x-amasi-signature",""),
                active_keys=active_keys,claim_nonce=claim_nonce,
                expected_path=CATALOG_PATH,now=now_fn()
            )
        except (ValueError,StageServiceUnauthorized):
            raise HTTPException(status_code=401,detail="service_unauthorized") from None
        try:
            payload=json.loads(body)
            if type(payload) is not dict or payload.get("action") not in ("search","product"):
                raise ValueError()
            action=payload["action"]
            if action=="product":
                if set(payload)!={"action","product_id"}:
                    raise ValueError()
                product_id=payload["product_id"]
                if type(product_id) is not str or not re.fullmatch(r"[1-9][0-9]{0,15}",product_id):
                    raise ValueError()
                url="/products/"+product_id
            else:
                if set(payload)!={"action","keyword"}:
                    raise ValueError()
                keyword=payload["keyword"]
                if type(keyword) is not str or not 2<=len(keyword.strip())<=80:
                    raise ValueError()
                if any(ord(c)<32 for c in keyword):
                    raise ValueError()
                url="/products?"+urlencode({"page":1,"per_page":5,
                                            "keyword":keyword.strip(),"status":"sale"})
        except (ValueError,TypeError,KeyError):
            raise HTTPException(status_code=400,detail="invalid_catalog_query") from None

        try:
            response=await call_salla(db,fixed_owner_id,"GET",url)
            fetched=now_fn()
            rows=_provider_payload(response,list_expected=(action=="search"))
            rows=rows if isinstance(rows,list) else [rows]
            items=[]
            for row in rows:
                try:
                    item=public_salla_product(row,fetched_at=fetched,
                                              detailed=(action=="product"))
                except InvalidProduct:
                    continue
                items.append(item)
                if len(items)>=5:
                    break
            return {
                "schema":"amasi_verified_salla_catalog_v1",
                "source_checked_at_utc":datetime.fromtimestamp(
                    fetched,timezone.utc).isoformat().replace("+00:00","Z"),
                "products":items,
                "quote_required":True,
            }
        except Exception:
            # Never return Salla token, raw product, provider errors, or costs.
            raise HTTPException(status_code=503,detail="catalog_unavailable") from None
    return router
