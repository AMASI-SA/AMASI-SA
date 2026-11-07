"""Synthetic backup contents verification; exact loopback-only boundary first."""
import hashlib
import sys
sys.path[:0] = ['/opt/acceptance', '/opt/mezan/backend']
from acceptance import boundary, db
from bson.binary import Binary
from cryptography.fernet import Fernet, InvalidToken

# Public synthetic fixture only, deliberately never injected into a hosted app.
KEY = b'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA='
BODY = b'%PDF-1.4 synthetic attachment, no customer data'


def seed():
    database = db()
    database.exit2d_restore_contract.create_index('business_id', unique=True)
    database.exit2d_restore_contract.insert_one({'_id': 'immutable-fixture-id',
        'business_id': 'synthetic-job-1', 'outcome': 'unknown', 'event_id': 'fixture-event',
        'encrypted': Fernet(KEY).encrypt(b'synthetic-only-token'), 'content': Binary(BODY),
        'sha256': hashlib.sha256(BODY).hexdigest()})


def verify():
    original = db()
    restored = original.client.mezan_exit2d_restore
    expected_names = sorted(original.list_collection_names())
    assert expected_names == sorted(restored.list_collection_names())
    from bson import json_util
    # Full document identity+bytes check, not just collection counts.
    for name in expected_names:
        source = sorted(json_util.dumps(row, sort_keys=True) for row in original[name].find())
        target = sorted(json_util.dumps(row, sort_keys=True) for row in restored[name].find())
        assert source == target, 'restored documents differ'
        assert original[name].index_information() == restored[name].index_information(), 'index definitions differ'
    row = restored.exit2d_restore_contract.find_one({'_id': 'immutable-fixture-id'})
    assert row['outcome'] == 'unknown' and row['event_id'] == 'fixture-event'
    assert Fernet(KEY).decrypt(row['encrypted']) == b'synthetic-only-token'
    try:
        Fernet(Fernet.generate_key()).decrypt(row['encrypted'])
    except InvalidToken:
        pass
    else:
        raise AssertionError('wrong key accepted')
    assert hashlib.sha256(bytes(row['content'])).hexdigest() == row['sha256']
    print('PASS synthetic dump/restore: all collections, full document identities/bytes, indexes, unknown outcome, decryption/wrong-key rejection and attachment checksum')


if __name__ == '__main__':
    boundary()
    {'seed': seed, 'verify': verify}[sys.argv[1]]()
