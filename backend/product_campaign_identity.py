"""Bounded, read-only recovery of explicit Snapchat campaign attribution.

Campaign ownership comes from native identity facts, never product similarity,
campaign names, conversion totals, or the currently selected account alone.
"""
from collections import defaultdict

from mezan_attribution_order_ledger import _explicit_hierarchy, _order_key, _provider
from salla_marketing_attribution import campaign_id_candidates, canonical_ad_platform

MAX_CANDIDATES = 2000


async def load_campaign_ledgers(db, owner, orders, ledger_by_key):
    from snapchat_v2.accounts import list_enabled_accounts
    from snapchat_v2.entities import SNAPCHAT_ENTITY_FACTS_COLLECTION

    result = defaultdict(list, {key: list(rows) for key, rows in ledger_by_key.items()})
    candidates = {id(order): set(campaign_id_candidates(order)) for order in orders
                  if canonical_ad_platform(order) == 'snapchat'}
    ids = set().union(*candidates.values()) if candidates else set()
    if not ids or len(ids) > MAX_CANDIDATES:
        return result
    accounts = await list_enabled_accounts(db, owner)
    account_ids = {str(row['ad_account_id']) for row in accounts if row.get('ad_account_id')}
    if not account_ids:
        return result
    facts = await db[SNAPCHAT_ENTITY_FACTS_COLLECTION].find(
        {'user_id': owner, 'provider': 'snapchat_ads', 'entity_type': 'campaign',
         'ad_account_id': {'$in': sorted(account_ids)}, 'external_id': {'$in': sorted(ids)}},
        {'_id': 0, 'user_id': 1, 'provider': 1, 'ad_account_id': 1,
         'external_id': 1, 'name': 1},
    ).max_time_ms(1500).to_list(length=MAX_CANDIDATES + 1)
    if len(facts) > MAX_CANDIDATES:
        return result
    identities = defaultdict(list)
    for fact in facts:
        if fact.get('user_id') == owner and fact.get('provider') == 'snapchat_ads' and fact.get('ad_account_id') in account_ids:
            identities[fact.get('external_id')].append(fact)
    for order in orders:
        key = _order_key(order)
        existing = result[key]
        if order.get('user_id') not in (None, owner) or len(existing) > 1:
            continue
        previous = existing[0].get('attribution', {}) if existing else {}
        if previous.get('quality') == 'ambiguous':
            continue
        matches = [fact for candidate in candidates.get(id(order), ()) for fact in identities[candidate]]
        if not matches:
            continue
        # Do not select the first match when raw/promoted facts disagree.
        if len(matches) != 1:
            result[key] = [{'attribution': {'quality': 'ambiguous', 'decision_safe': False}}]
            continue
        fact = matches[0]
        if previous.get('quality') == 'confirmed' and previous.get('decision_safe') is True:
            if any(previous.get(field) and str(previous[field]) != expected for field, expected in (
                    ('account_id', fact['ad_account_id']), ('campaign_id', fact['external_id']))) or (
                    previous.get('provider') and _provider(previous['provider']) != 'snapchat'):
                result[key] = [{'attribution': {'quality': 'ambiguous', 'decision_safe': False}}]
                continue
        result[key] = [{'order_key': key, 'user_id': owner, 'attribution': {
            **_explicit_hierarchy(order), **previous, 'provider': 'snapchat',
            'marketing_source': 'snapchat', 'account_id': fact['ad_account_id'],
            'campaign_id': fact['external_id'], 'campaign_name': fact.get('name'),
            'quality': 'confirmed', 'decision_safe': True,
            'match_method': 'explicit_campaign_id_native_identity',
        }}]
    return result
