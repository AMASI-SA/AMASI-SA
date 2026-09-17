"""Owner-scoped disposable-order console tests, disabled by default.

Mongo records serialize attempts across replicas. A lost response/crash keeps
its order lock; neither repeating execute nor preparing another plan replays it.
This is test infrastructure, not the general order editor or a raw API proxy.
"""
from __future__ import annotations

import os
import re
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

import order_revision_contracts as c

FIXTURES = 'order_revision_test_fixtures'
PLANS = 'order_revision_test_plans'
LOCKS = 'order_revision_test_locks'
PLAN_SECONDS = 300


def now():
    return datetime.now(timezone.utc)


def enabled():
    return os.environ.get('SALLA_ORDER_REVISION_CONSOLE_ENABLED', '').lower() == 'true'


def config(seed):
    return c.AmasiTestConfig(c.OFFICIAL_BASE_URL, str(seed.get('store_id', '')),
        frozenset({'orders.read_write', 'products.read', 'shipping.read'}),
        Path('.'), Path('.'), False, False, False, '', '', '')


def policy_digest(seed):
    policy = deepcopy(seed)
    # A later case can address only a line added by a verified earlier case.
    policy['orders'][0].pop('item_id', None)
    return c.canonical_digest(policy)


def validate_request(method, path):
    if method == 'GET' and re.fullmatch(r'/shipments\?order_id=[A-Za-z0-9._:-]+&per_page=1', path):
        return
    c.validate_endpoint(method, path)


class Provider:
    """Uses the existing encrypted integration; never accepts a client token."""
    def __init__(self, db, owner, store):
        self.db, self.owner, self.store = db, owner, store
        self.token = None

    async def __aenter__(self):
        from salla_integration.service import ensure_fresh_access_token
        rows = await self.db.salla_integrations.find({'user_id': self.owner}).limit(2).to_list(2)
        if len(rows) != 1 or str(rows[0].get('store_id')) != self.store:
            raise c.ContractRunnerError('integration_identity_mismatch')
        self.token = await ensure_fresh_access_token(self.db, self.owner,
            recover_needs_reauth=False, minimum_validity_sec=300)
        if not isinstance(self.token, str) or not self.token:
            raise c.ContractRunnerError('integration_unavailable')
        self.client = httpx.AsyncClient(timeout=30, follow_redirects=False)
        return self

    async def __aexit__(self, *_):
        self.token = None
        await self.client.aclose()

    async def request(self, method, path, body=None):
        # These paths originate only from validated contracts or fixed readers.
        validate_request(method, path)
        response = await self.client.request(method, c.OFFICIAL_BASE_URL + path,
            headers={'Authorization': 'Bearer ' + self.token}, json=body)
        try:
            payload = response.json()
        except ValueError:
            raise c.ContractRunnerError('provider_non_json') from None
        envelope = {'status': response.status_code, 'body': payload}
        if not c._amasi_response_success(envelope):
            raise c.ContractRunnerError('provider_request_rejected')
        return envelope


class RecordedShipment:
    def __init__(self, response):
        self.response = response

    def request(self, method, path, body, correlation):
        return self.response


async def shipment(provider, row):
    response = await provider.request('GET', f"/shipments?order_id={row['order_id']}&per_page=1")
    return c._verify_amasi_shipments(RecordedShipment(response), row, 'console-test')


def complete_items(response):
    body = response['body']
    rows, page = body.get('data'), body.get('pagination')
    if not isinstance(rows, list) or not rows or any(not isinstance(x, dict) for x in rows):
        raise c.ContractRunnerError('items_unproven')
    # The initial console test accepts only a complete one-page order.
    if not isinstance(page, dict) or any(type(page.get(k)) is not int for k in ('total', 'count', 'currentPage', 'totalPages')):
        raise c.ContractRunnerError('items_pagination_unproven')
    if (page['total'] != len(rows) or page['count'] != len(rows)
            or page['currentPage'] != 1 or page['totalPages'] != 1):
        raise c.ContractRunnerError('items_pagination_unproven')
    for links in (page, page.get('links', {}), body.get('links', {})):
        if links == []:
            continue
        if not isinstance(links, dict) or any(links.get(k) not in (None, '') for k in ('next', 'nextPage', 'next_page_url')):
            raise c.ContractRunnerError('items_pagination_unproven')
    ids = [c._id_text(x.get('id')) for x in rows]
    if len(set(ids)) != len(ids):
        raise c.ContractRunnerError('items_identity_unproven')
    return rows


async def snapshot(provider, seed, case, *, before):
    row = seed['orders'][0]
    store = (await provider.request('GET', '/store/info'))['body'].get('data')
    if not isinstance(store, dict) or str(store.get('id')) != str(seed['store_id']):
        raise c.ContractRunnerError('store_identity_mismatch')
    for product in seed['products']:
        data = (await provider.request('GET', '/products/' + str(product['product_id'])))['body'].get('data')
        if (not isinstance(data, dict) or str(data.get('id')) != str(product['product_id'])
                or str(data.get('sku')) != str(product['sku'])):
            raise c.ContractRunnerError('product_identity_mismatch')
        pairs, variants = c._seed_product_relations(product)
        actual_pairs, actual_variants = c._product_contract_relations(str(product['product_id']), data)
        expected_variants = {(x['product_id'], x['variant_id'], x['sku'], x['option_pairs']) for x in variants}
        if not pairs.issubset(actual_pairs) or not expected_variants.issubset(actual_variants):
            raise c.ContractRunnerError('product_options_changed')
    first_ship = await shipment(provider, row)
    order_response = await provider.request('GET', '/orders/' + str(row['order_id']))
    c._validate_amasi_order(order_response['body']['data'], row)
    items_response = await provider.request('GET', '/orders/items?order_id=' + str(row['order_id']))
    items = complete_items(items_response)
    order_response = await provider.request('GET', '/orders/' + str(row['order_id']))
    order = order_response['body']['data']
    c._validate_amasi_order(order, row)
    last_ship = await shipment(provider, row)
    if first_ship != last_ship:
        raise c.ContractRunnerError('shipment_changed')
    if str(row['preserve_item_id']) not in {str(x['id']) for x in items}:
        raise c.ContractRunnerError('original_item_missing')
    result = c.extract_snapshot(order_response, items_response)
    result.update(paid_amount=0, outstanding_amount=c._amount(order['payment_actions']['remaining_action']['remaining_amount']),
        payment_status='unpaid', shipment_count=last_ship['count'], shipment_review=last_ship)
    if before:
        targets = [x for x in items if str(x['id']) == str(row['item_id'])]
        if len(targets) != 1 or str(c._item_product_id(targets[0])) != str(row['product_id']) or str(targets[0].get('sku')) != str(row['sku']):
            raise c.ContractRunnerError('target_item_mismatch')
        expected = next(x['equals'] for x in case['assertions'] if x['path'] == 'before.order_total')
        if c._amasi_money(result['order_total'], 'SAR') != c._amasi_money(expected, 'SAR'):
            raise c.ContractRunnerError('baseline_total_changed')
    return result


def public(plan):
    # No customer identity, receipt reference, provider response or credentials.
    result = {k: plan[k] for k in ('plan_id', 'state', 'method', 'expires_at', 'result') if k in plan}
    if 'case' in plan:
        result['operation'] = {'path': plan['case']['path'], 'body': plan['case']['body'],
            'assertions': plan['case']['assertions']}
    return result


class ConsoleTests:
    def __init__(self, db, provider_factory=Provider):
        self.db, self.provider_factory = db, provider_factory

    async def prepare(self, owner, seed, case):
        if not enabled():
            raise c.ContractRunnerError('console_tests_disabled')
        # Explicitly reject Demo manifests, even if they contain valid test rows.
        if seed.get('classification') != c.AMASI_MANIFEST_CLASSIFICATION:
            raise c.ContractRunnerError('test_manifest_required')
        c.validate_seed_structure(config(seed), seed)
        c.validate_case(case, seed)
        if case.get('simulate_lost_response'):
            raise c.ContractRunnerError('loss_simulation_not_supported')
        key = c.canonical_digest([owner, str(seed['store_id']), str(case['order_id'])])
        if await self.db[LOCKS].find_one({'_id': key}):
            raise c.ContractRunnerError('order_attempt_locked')
        fixture = await self.db[FIXTURES].find_one({'_id': owner})
        if fixture and fixture['policy_digest'] != policy_digest(seed):
            raise c.ContractRunnerError('fixture_policy_changed')
        if case['method'] != 'POST':
            target = case['path'].rsplit('/', 1)[-1]
            if not fixture or target not in fixture.get('created_item_ids', []):
                raise c.ContractRunnerError('only_added_test_items_mutable')
        async with self.provider_factory(self.db, owner, str(seed['store_id'])) as provider:
            before = await snapshot(provider, seed, case, before=True)
        if not fixture:
            fixture = {'_id': owner, 'policy_digest': policy_digest(seed),
                'original_item_ids': [str(x['item_id']) for x in before['items']],
                'created_item_ids': [], 'created_at': now()}
            try:
                await self.db[FIXTURES].insert_one(fixture)
            except DuplicateKeyError:
                current = await self.db[FIXTURES].find_one({'_id': owner})
                if not current or current['policy_digest'] != fixture['policy_digest']:
                    raise c.ContractRunnerError('fixture_policy_changed') from None
        if case['method'] != 'POST' and case['path'].rsplit('/', 1)[-1] in fixture['original_item_ids']:
            raise c.ContractRunnerError('original_items_protected')
        plan_id = uuid.uuid4().hex
        plan = {'_id': plan_id, 'plan_id': plan_id, 'owner': owner, 'order_key': key,
            'state': 'prepared', 'method': case['method'], 'seed': deepcopy(seed),
            'case': deepcopy(case), 'before': before, 'before_digest': c.canonical_digest(before),
            'expires_at': now() + timedelta(seconds=PLAN_SECONDS)}
        await self.db[PLANS].insert_one(plan)
        return public(plan)

    async def read(self, owner, plan_id):
        plan = await self.db[PLANS].find_one({'_id': plan_id, 'owner': owner})
        if not plan:
            raise c.ContractRunnerError('plan_not_found')
        return public(plan)

    async def execute(self, owner, plan_id):
        if not enabled():
            raise c.ContractRunnerError('console_tests_disabled')
        plan = await self.db[PLANS].find_one_and_update(
            {'_id': plan_id, 'owner': owner, 'state': 'prepared', 'expires_at': {'$gt': now()}},
            {'$set': {'state': 'checking'}}, return_document=ReturnDocument.AFTER)
        if not plan:
            return await self.read(owner, plan_id)
        key = plan['order_key']
        try:
            await self.db[LOCKS].insert_one({'_id': key, 'owner': owner, 'plan_id': plan_id, 'created_at': now()})
        except DuplicateKeyError:
            await self.db[PLANS].update_one({'_id': plan_id}, {'$set': {'state': 'blocked', 'result': {'code': 'order_attempt_locked'}}})
            return await self.read(owner, plan_id)
        sent = False
        try:
            seed, case = plan['seed'], plan['case']
            fixture = await self.db[FIXTURES].find_one({'_id': owner})
            if not fixture or fixture['policy_digest'] != policy_digest(seed):
                raise c.ContractRunnerError('fixture_policy_changed')
            async with self.provider_factory(self.db, owner, str(seed['store_id'])) as provider:
                before = await snapshot(provider, seed, case, before=True)
                if c.canonical_digest(before) != plan['before_digest']:
                    raise c.ContractRunnerError('baseline_changed_prepare_again')
                expiry = plan['expires_at']
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if expiry <= now():
                    raise c.ContractRunnerError('plan_expired')
                # Persist intent BEFORE the first and only mutation request.
                await self.db[PLANS].update_one({'_id': plan_id}, {'$set': {'state': 'in_flight', 'sent_at': now()}})
                sent = True
                await provider.request(case['method'], case['path'], case['body'] or None)
                after = await snapshot(provider, seed, case, before=False)
                checks = c.evaluate_fixed_postconditions(case, seed, before, after) + c.execute_assertions(case['assertions'], before, after)
                passed = all(x.get('passed') is True for x in checks)
                result = {'observed_verdict': 'PASS' if passed else 'FAIL',
                    'webhook_verdict': 'NOT_VERIFIED', 'verdict': 'INCONCLUSIVE',
                    'failed_checks': [x.get('name', x.get('path')) for x in checks if x.get('passed') is not True],
                    'before_total': before['order_total'], 'after_total': after['order_total']}
                if not passed:
                    await self.db[PLANS].update_one({'_id': plan_id}, {'$set': {'state': 'quarantined', 'result': result}})
                    return await self.read(owner, plan_id)
                added = sorted({str(x['item_id']) for x in after['items']} - {str(x['item_id']) for x in before['items']})
                if added:
                    await self.db[FIXTURES].update_one({'_id': owner}, {'$addToSet': {'created_item_ids': {'$each': added}}})
                result['added_item_ids'] = added
                await self.db[PLANS].update_one({'_id': plan_id}, {'$set': {'state': 'observed', 'result': result}})
            await self.db[LOCKS].delete_one({'_id': key, 'plan_id': plan_id})
        except Exception as exc:
            code = str(exc) if isinstance(exc, c.ContractRunnerError) else 'test_execution_unavailable'
            # Exception/provider text is never exposed. Unknown outcomes retain
            # the durable order lock, including when result persistence fails.
            await self.db[PLANS].update_one({'_id': plan_id}, {'$set': {
                'state': 'unknown' if sent else 'blocked', 'result': {'code': code, 'retry_allowed': False}}})
            if not sent:
                await self.db[LOCKS].delete_one({'_id': key, 'plan_id': plan_id})
        return await self.read(owner, plan_id)
