"""Operator-only passive pilot for one owner-authorized public product.

NOT a generic URL service or production route. No model/tenant/customer data.
The worker downloads bounded HTML only at Browserless, returns allowlisted
facts, and never sends HTML/media to Mezan. Dynamic price/tracking not checked.
"""
import asyncio
import json
from pathlib import Path
import secrets
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from external_browser_connection import ENDPOINT

WORKER = Path(__file__).with_name('browser_workers') / 'public_product_pilot.mjs'
TARGET = 'https://amasi-sa.com/ar/طقم-سلسال-بتعليقات-مستطيلة-لامعة/p775397267'


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)


class Option(Strict):
    label: str = Field(max_length=120)
    values: list[str] = Field(max_length=20)


class DefinitionValue(Strict):
    id: str | None = Field(max_length=120)
    label: str = Field(max_length=120)
    additional_price: float | None = Field(ge=-1000000000, le=1000000000, allow_inf_nan=False)
    unavailable: bool | None


class Definition(Strict):
    id: str | None = Field(max_length=120)
    label: str = Field(max_length=120)
    type: str = Field(max_length=80)
    required: bool
    conditional: bool
    values: list[DefinitionValue] = Field(max_length=20)


class Facts(Strict):
    title: str = Field(max_length=300)
    heading: str = Field(max_length=300)
    description: str = Field(max_length=1200)
    options: list[Option] = Field(max_length=10)
    options_truncated: bool
    option_definition_status: Literal['unavailable','invalid','observed','truncated'] = 'unavailable'
    option_definitions: list[Definition] = Field(default_factory=list, max_length=20)
    displayed_price_text: str = Field(default='', max_length=120)


class Response(Strict):
    protocol: Literal['mezan-passive-product-v1']
    nonce: str
    status: Literal['observed','blocked','unavailable','too_large','worker_error']
    bytes_read: int = Field(ge=0,le=1016384)
    blocked_requests: int = Field(ge=0,le=100000)
    facts: Facts | None


async def collect_passive_pilot(token, *, transport=None):
    return await _collect_passive(token, transport=transport)


async def _collect_passive(token, *, transport=None, target=None):
    def result(status, **facts):
        return dict(status=status, mode='passive_html_only', target=target or TARGET,
            dynamic_options_checked=False, tracking_delivery_checked=False,
            media_returned=False, **facts)
    if not token or not token.strip():
        return result('not_configured')
    if token != token.strip() or any(ord(c)<33 or ord(c)>126 for c in token):
        return result('invalid_configuration')
    nonce = secrets.token_hex(16)
    try:
        async with asyncio.timeout(35):
            async with httpx.AsyncClient(transport=transport,timeout=30,follow_redirects=False,trust_env=False) as client:
                async with client.stream('POST',ENDPOINT,
                    params={'timeout':'25000','launch':json.dumps({'record':False,'replay':False})},
                    headers={'Authorization':f'Bearer {token}','Accept':'application/json','Accept-Encoding':'identity'},
                    json={'code':WORKER.read_text(), 'context':{'nonce':nonce, **({'target':target} if target else {})}}) as response:
                    if response.status_code != 200:
                        return result({400:'request_rejected',401:'access_denied',403:'access_denied',408:'timeout',429:'capacity_unavailable'}.get(response.status_code,'service_unavailable'))
                    if response.headers.get('content-type','').split(';')[0].strip() != 'application/json' or response.headers.get('content-encoding','identity') != 'identity':
                        return result('unexpected_response')
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=1024):
                        if len(body)+len(chunk)>65536:
                            return result('unexpected_response')
                        body.extend(chunk)
                    data = json.loads(body)
                    if isinstance(data,dict) and set(data)=={'data','type'} and data['type']=='application/json':
                        data=data['data']
                    parsed=Response.model_validate(data)
                    if parsed.nonce != nonce or (parsed.status=='observed') != (parsed.facts is not None):
                        return result('unexpected_response')
                    if parsed.facts and any(len(v)>120 for o in parsed.facts.options for v in o.values):
                        return result('unexpected_response')
                    return result(parsed.status, facts=parsed.facts.model_dump() if parsed.facts else None,
                        bytes_read=parsed.bytes_read,blocked_requests=parsed.blocked_requests)
    except (TimeoutError,httpx.TimeoutException):
        return result('timeout')
    except (ValueError,UnicodeError):
        return result('unexpected_response')
    except httpx.HTTPError:
        return result('service_unavailable')
