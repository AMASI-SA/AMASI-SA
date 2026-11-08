"""Bounded, read-only recovery of explicit Snapchat campaign attribution.

Campaign ownership comes from native identity facts, never product similarity,
campaign names, conversion totals, or the currently selected account alone.
"""
from collections import defaultdict
import re

from mezan_attribution_order_ledger import _explicit_hierarchy, _order_key, _provider
from salla_marketing_attribution import SALLA_RAW_ATTRIBUTION_PROJECTION, campaign_id_candidates, canonical_ad_platform, field_values

MAX_CANDIDATES = 2000


def campaign_attribution_projection():
    """Same approved marketing leaves for promoted and original Salla facts."""
    projection = dict(SALLA_RAW_ATTRIBUTION_PROJECTION)
    prefix = 'raw_by_source.salla_direct.'
    projection.update({path.removeprefix(prefix): 1 for path in projection if path.startswith(prefix)})
    sources = {path.split('.source.', 1)[0] + '.source' for path in projection if '.source.' in path}
    sources.add('source')
    projection.update({path: 1 for path in sources})
    return {path: value for path, value in projection.items()
            if not any(path.startswith(parent + '.') for parent in projection if parent != path)}


async def load_campaign_ledgers(db, owner, orders, ledger_by_key):
    from snapchat_v2.accounts import list_enabled_accounts
    from snapchat_v2.entities import SNAPCHAT_ENTITY_FACTS_COLLECTION

    result = defaultdict(list, {key: list(rows) for key, rows in ledger_by_key.items()})
    candidates = {id(order): {value for value in campaign_id_candidates(order) if len(value) <= 128} for order in orders
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
        previous_source = _provider(previous.get('provider')) or previous.get('marketing_source')
        if previous_source and previous_source != 'snapchat':
            result[key] = [{'attribution': {'quality': 'ambiguous', 'decision_safe': False}}]
            continue
        explicit_ids = field_values(order, 'campaign_id', 'source_campaign_id', 'utm_campaign_id', 'ad_campaign_id')
        explicit_ids += [value for value in candidates.get(id(order), ())
                         if value.isdigit() or re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value)]
        if any(value != fact['external_id'] for value in explicit_ids):
            result[key] = [{'attribution': {'quality': 'ambiguous', 'decision_safe': False}}]
            continue
        if previous.get('quality') == 'confirmed' and previous.get('decision_safe') is True:
            if any(previous.get(field) and str(previous[field]) != expected for field, expected in (
                    ('account_id', fact['ad_account_id']), ('campaign_id', fact['external_id']))) or (
                    previous.get('provider') and _provider(previous['provider']) != 'snapchat'):
                result[key] = [{'attribution': {'quality': 'ambiguous', 'decision_safe': False}}]
                continue
        hierarchy = _explicit_hierarchy(order)
        hierarchy_conflict = any(previous.get(field) and value and previous[field] != value
                                 for field, value in hierarchy.items())
        hierarchy_conflict |= any(len(field_values(order, *fields)) > 1 for fields in (
            ('ad_id', 'source_ad_id', 'advertisement_id'),
            ('ad_group_id', 'adgroup_id', 'ad_squad_id', 'source_ad_group_id')))
        hierarchy = {field: value or previous.get(field) for field, value in hierarchy.items()}
        if hierarchy_conflict:
            hierarchy = {field: None for field in hierarchy}
        result[key] = [{'order_key': key, 'user_id': owner, 'attribution': {
            **previous, **hierarchy, 'provider': 'snapchat',
            'marketing_source': 'snapchat', 'account_id': fact['ad_account_id'],
            'campaign_id': fact['external_id'], 'campaign_name': fact.get('name'),
            'quality': 'confirmed', 'decision_safe': True,
            'match_method': 'explicit_campaign_id_native_identity',
        }}]
    return result
