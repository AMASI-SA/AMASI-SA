"""Attach one AMASI public product observation to existing scoped evidence.

No discovery by campaign name, no invented product association, no global cache
or customer information. The caller supplies the owner-resolved product pack.
"""
from external_amasi_storefront import amasi_browser_enabled, collect_amasi_product


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
                product['storefront_observation'] = seen[key]
                continue
            if seen:
                product['storefront_observation'] = {'status':'per_analysis_limit','performed':False}
                continue
            observation = await collect_amasi_product(user_id, pid, url)
            product['storefront_observation'] = observation
            # Invalid targets do not consume an external session or the visit quota.
            if observation['status'] != 'outside_amasi_product_scope':
                seen[key] = observation


def research_status_for_products(products):
    observations = [p.get('storefront_observation') or {} for p in products]
    observed = [o for o in observations if o.get('performed') is True and o.get('status')=='observed']
    if not observations or not any(observations):
        from campaign_ai_research_contract import external_research_status
        return external_research_status()
    return {'version':1, 'status':'partial' if observed else 'not_observed',
            'performed':bool(observed), 'mode':'passive_html_only',
            'products_observed':len({o.get('product_id') for o in observed}),
            'areas_observed':['storefront_text','declared_options'] if observed else [],
            'areas_unverified':['selected_final_price','checkout','tracking','competitors','seasonality','visual_design'],
            'message':('فُحص نص صفحة منتج أماسي وخياراتها المعلنة. السعر النهائي بعد الاختيار والدفع والبكسل والمنافسون لم تُختبر.'
                       if observed else 'لم تكتمل قراءة صفحة المنتج؛ يستمر التحليل بالأدلة المتاحة.')}


def research_status_for_pack(pack):
    return research_status_for_products([p for block in (pack.get('entities') or {}).values() for p in block.get('products') or []])
