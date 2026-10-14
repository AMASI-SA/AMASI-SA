"""Bounded AMASI-only passive product collection; no general URL endpoint.

Caller must resolve products through the existing owner/campaign association and
owner-filtered catalog. This second boundary limits operator-enabled owner and
exact product identity. External browser receives a public URL and nonce only.
"""
import asyncio
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, unquote, quote

from external_storefront_pilot import _collect_passive

_slot = asyncio.Semaphore(1)


def canonical_amasi_product_url(value, product_id):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,20}', str(product_id)):
        return None
    if len(value) > 2000 or any(ord(c) <= 32 or ord(c) == 127 for c in value) or '\\' in value:
        return None
    try:
        url = urlsplit(value)
        path = unquote(url.path, errors='strict')
        if (url.scheme != 'https' or url.netloc != 'amasi-sa.com' or url.query or url.fragment
                or any(ord(c) <= 32 or ord(c) == 127 for c in path)
                or '%' in path or '\\' in path or any(p in {'.','..'} for p in path.split('/'))
                or not re.fullmatch(r'/(?:ar/)?(?:[^/]+/)?p' + re.escape(str(product_id)), path)):
            return None
        return 'https://amasi-sa.com' + quote(path, safe='/-._~')
    except (ValueError, UnicodeError):
        return None


def amasi_browser_enabled(user_id):
    enabled_owner = os.environ.get('MEZAN_AMASI_BROWSER_OWNER_ID', '').strip()
    return bool(enabled_owner and str(user_id) == enabled_owner)


async def collect_amasi_product(user_id, product_id, canonical_url, *, token=None, transport=None):
    result = {'status':'not_enabled','performed':False,'mode':'passive_html_only',
              'dynamic_options_checked':False,'tracking_delivery_checked':False,'media_returned':False}
    if not amasi_browser_enabled(user_id):
        return result
    target = canonical_amasi_product_url(canonical_url, product_id)
    if target is None:
        return {**result, 'status':'outside_amasi_product_scope'}
    if _slot.locked():
        return {**result, 'status':'busy'}
    async with _slot:
        observation = await _collect_passive(
            os.environ.get('BROWSERLESS_API_TOKEN','') if token is None else token,
            transport=transport, target=target)
    return {**observation, 'performed':observation['status']=='observed',
            'product_id':str(product_id), 'observed_at':datetime.now(timezone.utc).isoformat(),
            'unverified_areas':['selected_final_price','checkout','pixel_delivery','competitors','seasonality','visual_design']}
