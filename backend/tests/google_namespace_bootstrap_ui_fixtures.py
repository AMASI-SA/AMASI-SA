"""Synthetic public fixtures from frozen runtime serializers, not live DB evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import json
from pathlib import Path

from integrations_control_center import google_namespace_bootstrap as bootstrap
from tests.test_google_namespace_bootstrap import prepared

STAMP = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
PATH = Path(__file__).resolve().parents[2] / 'frontend/src/services/__fixtures__/googleNamespaceBootstrap.json'


def public_fixtures():
    owner = {'id': 'owner', 'role': 'owner'}
    with patch.object(bootstrap, '_now', lambda: STAMP):
        row = prepared(owner)
        bootstrap._valid_receipt(row)
        result = {'no_preparation': bootstrap._public_receipt(None, owner),
            'prepared': bootstrap._public_receipt(row, owner),
            'foreign': bootstrap._public_receipt(row, {'id': 'another', 'role': 'owner'}),
            'noop': bootstrap._result('already_compatible_empty_noop', namespace_contract_verified=True),
            'verified': bootstrap._result('verified_at_observation', namespace_contract_verified=True,
                receipt_retained=True, execution_token_consumed=True, created_labels=list(bootstrap.TARGETS))}
        expired = deepcopy(row)
        expired['prepared_at'] -= timedelta(hours=1); expired['expires_at'] -= timedelta(hours=1)
        result['expired'] = bootstrap._public_receipt(bootstrap._valid_receipt(expired), owner)
        for state in ('consumed', 'applied', 'verified'):
            candidate = deepcopy(row); candidate.update(state=state, attempt_id='c' * 32)
            if state == 'verified': candidate.update(verified_at=STAMP, verified_catalog_digest='d' * 64)
            result['historical' if state == 'verified' else state] = bootstrap._public_receipt(bootstrap._valid_receipt(candidate), owner)
        return result


def assert_public_fixture(actual, key):
    """Normalize only checked random/time bindings, never public authority or states."""
    expected = public_fixtures()[key]
    candidate = deepcopy(actual)
    if key == 'prepared':
        import re
        assert set(candidate) == set(expected)
        for field, length in [('request_nonce', 32), ('execution_token', 64), ('observation_digest', 64)]:
            assert isinstance(candidate[field], str) and re.fullmatch('[0-9a-f]{' + str(length) + '}', candidate[field])
            candidate[field] = expected[field]
        assert candidate['manifest_digest'] == bootstrap.MANIFEST_DIGEST
        assert isinstance(candidate['expires_at'], str)
        assert datetime.fromisoformat(candidate['expires_at']).utcoffset() == timedelta(0)
        candidate['expires_at'] = expected['expires_at']
    assert candidate == expected


if __name__ == '__main__':
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(public_fixtures(), ensure_ascii=False, indent=2) + '\n')
