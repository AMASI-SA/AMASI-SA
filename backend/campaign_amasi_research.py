"""Attach one AMASI public product observation to existing scoped evidence.

No discovery by campaign name, no invented product association, no global cache
or customer information. The caller supplies the owner-resolved product pack.
"""
from external_amasi_storefront import amasi_browser_enabled, canonical_amasi_product_url, collect_amasi_product


async def enrich_amasi_products(user_id, product_evidence):
    if not amasi_browser_enabled(user_id):
        return
    seen = {}
    for block in (product_evidence.get('entities') or {}).values():
        for product in block.get('products') or []:
            pid = str(product.get('product_id') or '')
            url = product.get('canonical_product_url')
            key = (pid, url)
            if key in seen:
                _attach(product, seen[key])
                continue
            if seen:
                _attach(product, {'status':'per_analysis_limit','performed':False})
                continue
            observation = await collect_amasi_product(user_id, pid, url)
            _attach(product, observation)
            # Invalid targets do not consume an external session or the visit quota.
            if observation['status'] != 'outside_amasi_product_scope':
                seen[key] = observation


def _attach(product, observation):
    product['storefront_observation'] = observation
    pid = product.get('product_id')
    destination = canonical_amasi_product_url(product.get('destination_url'), pid)
    canonical = canonical_amasi_product_url(product.get('canonical_product_url'), pid)
    matched = bool(product.get('destination_source') == 'provider_ad_metadata'
                   and destination and canonical and destination == canonical)
    visited = observation.get('performed') is True and observation.get('status') == 'observed'
    facts = observation.get('facts') or {}
    # Keep the existing page-health seam. A canonical visit does not validate
    # a different ad destination, cart functionality, or option-selected price.
    product['page_probe'] = {
        'checked': visited and matched,
        'status': 'PRODUCT_URL_OK' if visited and matched else 'PRODUCT_URL_UNKNOWN',
        'reason': 'passive_canonical_html_observed' if visited and matched else 'destination_not_observed',
        'page_title': facts.get('title') if visited else None,
        'meta_description': facts.get('description') if visited else None,
        'add_to_cart_marker_present': None,
        'synthetic_check_scope':'passive_html_no_cart_or_media',
    }


def research_status_for_products(products):
    observations = [p.get('storefront_observation') or {} for p in products]
    observed = [o for o in observations if o.get('performed') is True and o.get('status')=='observed']
    if not observations or not any(observations):
        from campaign_ai_research_contract import external_research_status
        return external_research_status()
    return {'version':1, 'status':'partial' if observed else 'not_observed',
            'performed':bool(observed), 'mode':'passive_html_only',
            'products_observed':len({o.get('product_id') for o in observed}),
            'areas_observed':(['storefront_text'] + (['declared_options'] if any((o.get('facts') or {}).get('option_definition_status') in {'observed','truncated'} for o in observed) else [])) if observed else [],
            'areas_unverified':['selected_final_price','checkout','tracking','competitors','seasonality','visual_design'],
            'message':('فُحص نص صفحة منتج أماسي وما توفر من بيانات خياراتها. السعر النهائي بعد الاختيار والدفع والبكسل والمنافسون لم تُختبر.'
                       if observed else 'لم تكتمل قراءة صفحة المنتج؛ يستمر التحليل بالأدلة المتاحة.')}


def research_status_for_pack(pack):
    return research_status_for_products([p for block in (pack.get('entities') or {}).values() for p in block.get('products') or []])
