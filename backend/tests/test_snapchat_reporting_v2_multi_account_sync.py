"""Account targeting must not mutate the user's default display selection."""
import pytest

from tests.test_snapchat_reporting_v2_refresh_visibility import Database
from snapchat_v2 import accounts, connection, scheduler, status


def seeded():
    db = Database()
    for user, account, selected, active, provider, connected in [
        ('u', 'primary', True, True, 'snapchat_ads', 'connected'),
        ('u', 'second', False, True, 'snapchat_ads', 'connected'),
        ('other', 'foreign', True, True, 'snapchat_ads', 'connected'),
        ('u', 'inactive', False, False, 'snapchat_ads', 'connected'),
        ('u', 'meta', False, True, 'meta_ads', 'connected'),
        ('u', 'offline', False, True, 'snapchat_ads', 'stale'),
    ]:
        db.raw[accounts.SNAPCHAT_ACCOUNTS_COLLECTION].insert_one(dict(
            user_id=user, ad_account_id=account, selected=selected, active=active,
            provider=provider, connection_status=connected))
        db.raw[accounts.LEGACY_INTEGRATION_ACCOUNTS_COLLECTION].insert_one(dict(
            user_id=user, external_account_id=account, provider=provider,
            mezan_selected=account in {'primary', 'second', 'foreign'},
            connection_status=connected))
    return db


class Tokens:
    async def get_access_token(self, user_id):
        return 'test-token'


@pytest.mark.asyncio
async def test_explicit_ready_does_not_change_selection():
    db = seeded()
    before = list(db.raw[accounts.SNAPCHAT_ACCOUNTS_COLLECTION].find())
    token, account = await connection.SnapchatConnectionManager(db, token_store=Tokens()).validate_ready(
        'u', ad_account_id='second')
    assert token == 'test-token'
    assert account['ad_account_id'] == 'second'
    assert list(db.raw[accounts.SNAPCHAT_ACCOUNTS_COLLECTION].find()) == before
    assert (await accounts.get_selected_account(db, 'u'))['ad_account_id'] == 'primary'


@pytest.mark.asyncio
async def test_scheduler_includes_second_account_and_filters_unavailable():
    result = await scheduler._selected_accounts(seeded())
    assert {r['ad_account_id'] for r in result} == {'primary', 'second', 'foreign'}


@pytest.mark.asyncio
async def test_status_targets_requested_account_without_selection_write():
    db = seeded()
    result = await status.snapchat_v2_status(db, 'u', 'second')
    assert result['account_id'] == 'second'
    assert result['selected_account']['ad_account_id'] == 'second'
    assert (await accounts.get_selected_account(db, 'u'))['ad_account_id'] == 'primary'


@pytest.mark.asyncio
@pytest.mark.parametrize('account_id', ['foreign', 'inactive', 'meta', 'missing', ''])
async def test_unknown_explicit_account_never_falls_back(account_id):
    db = seeded()
    with pytest.raises(LookupError):
        await accounts.resolve_account(db, 'u', account_id)
    with pytest.raises(LookupError):
        await status.snapchat_v2_status(db, 'u', account_id)
    with pytest.raises(LookupError):
        await connection.SnapchatConnectionManager(db, token_store=Tokens()).validate_ready(
            'u', ad_account_id=account_id)


@pytest.mark.asyncio
async def test_default_resolution_preserves_legacy_selection():
    assert (await accounts.resolve_account(seeded(), 'u'))['ad_account_id'] == 'primary'


@pytest.mark.asyncio
async def test_cycle_continues_other_accounts_after_one_failure(monkeypatch):
    db = seeded()
    seen = []

    class Pipeline:
        def __init__(self, database):
            assert database is db

        async def run(self, user_id, ad_account_id, **kwargs):
            seen.append((user_id, ad_account_id))
            if ad_account_id == 'primary':
                raise RuntimeError('provider temporary error')
            return {'status': 'complete', 'sync_run_id': ad_account_id}

    monkeypatch.setattr(scheduler, 'SnapchatV2SyncPipeline', Pipeline)
    result = await scheduler.run_shadow_cycle(db)
    assert set(seen) == {('u', 'primary'), ('u', 'second'), ('other', 'foreign')}
    assert result['accounts_found'] == 3
    assert result['completed'] == 2
    assert result['failed'] == 1
    assert (await accounts.get_selected_account(db, 'u'))['ad_account_id'] == 'primary'


@pytest.mark.asyncio
async def test_legacy_fallback_remains_selected_only():
    db = Database()
    for selected, account in [(True, 'legacy-primary'), (False, 'legacy-secondary')]:
        db.raw[accounts.LEGACY_INTEGRATION_ACCOUNTS_COLLECTION].insert_one(dict(
            user_id='u', provider='snapchat_ads', external_account_id=account,
            mezan_selected=selected, connection_status='connected'))
    assert await scheduler._selected_accounts(db) == [
        {'user_id': 'u', 'ad_account_id': 'legacy-primary'}]


@pytest.mark.asyncio
async def test_disabled_discovered_account_never_scheduled_or_resolved():
    db = seeded()
    db.raw[accounts.LEGACY_INTEGRATION_ACCOUNTS_COLLECTION].update_many(
        {'user_id': 'u'}, {'$set': {'mezan_selected': False}})
    assert await accounts.list_enabled_accounts(db, 'u') == []
    assert not any(row['user_id'] == 'u' for row in await scheduler._selected_accounts(db))
    with pytest.raises(LookupError):
        await accounts.resolve_account(db, 'u', 'primary')
    assert await accounts.resolve_account(db, 'u') is None
    with pytest.raises(connection.SnapchatConnectionError, match='No primary'):
        await connection.SnapchatConnectionManager(db, token_store=Tokens()).validate_ready('u')


@pytest.mark.asyncio
async def test_disabled_default_does_not_silently_switch_to_other_enabled_account():
    db = seeded()
    db.raw[accounts.LEGACY_INTEGRATION_ACCOUNTS_COLLECTION].update_one(
        {'user_id': 'u', 'external_account_id': 'primary'},
        {'$set': {'mezan_selected': False}})
    assert [row['ad_account_id'] for row in await accounts.list_enabled_accounts(db, 'u')] == ['second']
    assert await accounts.resolve_account(db, 'u') is None


@pytest.mark.asyncio
async def test_v2_only_installation_defaults_to_selected_not_all_discovery():
    db = seeded()
    db.raw[accounts.LEGACY_INTEGRATION_ACCOUNTS_COLLECTION].delete_many({})
    assert [row['ad_account_id'] for row in await accounts.list_enabled_accounts(db, 'u')] == ['primary']
    with pytest.raises(LookupError):
        await accounts.resolve_account(db, 'u', 'second')


@pytest.mark.asyncio
async def test_disabled_discovery_does_not_consume_scheduler_enabled_budget():
    db = seeded()
    for index in range(60):
        db.raw[accounts.SNAPCHAT_ACCOUNTS_COLLECTION].insert_one(dict(
            user_id='u', provider='snapchat_ads', ad_account_id=f'discovered-{index}',
            active=True, selected=False, connection_status='connected'))
    assert {row['ad_account_id'] for row in await scheduler._selected_accounts(db)} == {
        'primary', 'second', 'foreign'}


@pytest.mark.asyncio
async def test_enabled_account_budget_still_fails_closed():
    db = Database()
    for index in range(scheduler.MAX_SCHEDULER_ACCOUNTS + 1):
        identity = dict(user_id='u', provider='snapchat_ads', ad_account_id=f'enabled-{index}',
                        connection_status='connected')
        db.raw[accounts.SNAPCHAT_ACCOUNTS_COLLECTION].insert_one(
            dict(identity, active=True, selected=index == 0))
        db.raw[accounts.LEGACY_INTEGRATION_ACCOUNTS_COLLECTION].insert_one(
            dict(identity, mezan_selected=True))
    with pytest.raises(RuntimeError, match='enabled account limit'):
        await scheduler._selected_accounts(db)

