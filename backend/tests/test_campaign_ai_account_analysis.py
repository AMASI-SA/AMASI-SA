import asyncio
from datetime import date

import campaign_ai_policy_v2 as policy


def diagnostic(account_id, eligible=True, reason=None):
    return {
        'account': {'id': account_id, 'name': account_id},
        'period': {'date_from': '2026-10-13', 'date_to': '2026-10-15', 'timezone': 'Asia/Riyadh'},
        'decision_eligibility': {'eligible': eligible, 'reason': reason or 'ready',
                                 'blocked_by': [] if eligible else ['range_incomplete'],
                                 'gates': {'coverage': eligible}},
    }


def row(account_id, provider='snapchat'):
    return {'provider': provider, 'account_id': account_id, 'entity_id': 'c1'}


def test_two_accounts_keep_blocked_period_without_inventing_recommendations():
    result = policy._account_analysis(
        [diagnostic('sa', False), diagnostic('us')],
        [row('us'), row('meta', 'meta')], [row('us'), row('meta', 'meta')],
        [row('meta', 'meta')], 'openai',
    )
    by_id = {item['account_id']: item for item in result}
    assert by_id['sa']['status'] == 'blocked'
    assert by_id['sa']['period']['date_from'] == '2026-10-13'
    assert by_id['sa']['blocked_by'] == ['range_incomplete']
    assert by_id['us']['status'] == 'no_recommendation'
    assert by_id['us']['candidates_scanned'] == 1
    assert by_id['us']['recommendations_count'] == 0
    assert by_id['meta']['status'] == 'recommendations_available'


def test_no_candidates_model_failure_and_missing_evidence_are_distinct():
    assert policy._account_analysis([diagnostic('sa')], [], [], [], 'none')[0]['status'] == 'no_candidates'
    assert policy._account_analysis([diagnostic('sa')], [row('sa')], [row('sa')], [], 'openai_unavailable')[0]['status'] == 'analysis_failed'
    assert policy._account_analysis([], [row('sa')], [], [], 'none')[0]['status'] == 'no_candidates'
    failure = diagnostic('sa', False, 'account_evidence_load_failed')
    assert policy._account_analysis([failure], [], [], [], 'none')[0]['status'] == 'analysis_failed'


def test_repeated_reads_cannot_overwrite_blocked_account_or_cross_account_counts():
    result = policy._account_analysis(
        [diagnostic('sa', False), diagnostic('sa'), diagnostic('us')],
        [row('us')], [row('us')], [row('us')], 'openai',
    )
    assert len(result) == 2
    assert result[0]['status'] == 'blocked'
    assert result[0]['recommendations_count'] == 0
    assert result[1]['status'] == 'recommendations_available'


def test_wrapper_preserves_diagnostics_and_has_no_cross_user_state(monkeypatch):
    async def load(_db, user_id, *_args):
        return {'campaigns': [row(user_id)], 'children': [],
                'account_diagnostics': [diagnostic(user_id)]}
    monkeypatch.setattr(policy, 'load_snapchat_unified_ai_entities', load)
    async def run():
        return await asyncio.gather(*[
            policy._snapchat_campaign_entities(None, user, date(2026, 10, 13), date(2026, 10, 15))
            for user in ('owner-a', 'owner-b')
        ])
    first, second = asyncio.run(run())
    assert first == [row('owner-a')]
    assert first.account_diagnostics[0]['account']['id'] == 'owner-a'
    assert second.account_diagnostics[0]['account']['id'] == 'owner-b'


def test_monitor_persists_blocked_account_when_only_meta_has_candidates(monkeypatch):
    class Collection:
        def __init__(self):
            self.inserted = []
        async def insert_one(self, document):
            self.inserted.append(document)
        async def update_one(self, *args, **kwargs):
            pass
    class FakeDB(dict):
        def __missing__(self, name):
            self[name] = Collection()
            return self[name]
    from datetime import datetime, timezone
    async def campaigns(_db, _user, provider, *_args):
        if provider == 'snapchat':
            return policy._AccountEntityRows({'account_diagnostics': [diagnostic('sa', False)]}, 'campaigns')
        return [row('meta', 'meta')]
    async def empty(*_args, **_kwargs):
        return []
    async def context(*_args, **_kwargs):
        return {}
    async def model(*_args, **_kwargs):
        return policy.RecommendationOutput(summary='No decisions', recommendations=[], limitations=[])
    monkeypatch.setattr(policy, '_campaign_entities', campaigns)
    monkeypatch.setattr(policy, '_snapchat_child_entities', empty)
    monkeypatch.setattr(policy._legacy, '_meta_child_entities', empty)
    monkeypatch.setattr(policy._legacy, '_campaign_history_context', context)
    monkeypatch.setattr(policy, '_experiment_outcomes_context', context)
    monkeypatch.setattr(policy._legacy, '_business_profit_context', context)
    monkeypatch.setattr(policy._legacy, 'deterministic_candidates', lambda rows: rows)
    monkeypatch.setattr(policy, '_ask_openai', model)
    db = FakeDB()
    result = asyncio.run(policy.run_campaign_ai_monitor(
        db, 'owner', refresh_meta=False,
        now=lambda: datetime(2026, 10, 16, 20, tzinfo=timezone.utc),
    ))
    persisted = db[policy.RECOMMENDATION_COLLECTION].inserted[0]
    assert persisted['account_analysis'] == result['account_analysis']
    assert persisted['account_analysis'][0]['status'] == 'blocked'
    assert persisted['account_analysis'][0]['entities_scanned'] == 0
    assert persisted['recommendations'] == []
    assert persisted['writes_performed'] is False
