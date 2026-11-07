"""Full synthetic-volume recovery receipt; no Production URI accepted."""
import hashlib
import json
from pathlib import Path
import sys

sys.path[:0] = ['/opt/mezan/backend', '/evidence/acceptance', '/evidence/tools']
from independent_staging import validate
validate()
import os
assert os.environ['MONGO_URL'] == 'mongodb://fixture:synthetic-only-password@staging-mongo:27017'
assert os.environ.get('EXIT2D_ECONOMY_CI') == '1'
from pymongo import MongoClient
from bson import json_util
from bson.binary import Binary
from cryptography.fernet import Fernet, InvalidToken

KEY = b'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA='
BODY = b'%PDF-1.4 synthetic VPS attachment only'
db = MongoClient(os.environ['MONGO_URL'])['mezan_exit2d_staging']

def snapshot():
    result = {}
    for name in sorted(db.list_collection_names()):
        data = '\n'.join(sorted(json_util.dumps(row, sort_keys=True) for row in db[name].find())).encode()
        indexes = json_util.dumps(db[name].index_information(), sort_keys=True).encode()
        result[name] = {'documents_sha256': hashlib.sha256(data).hexdigest(),
            'indexes_sha256': hashlib.sha256(indexes).hexdigest(), 'count': db[name].count_documents({})}
    return result

phase, path = sys.argv[1:]
if phase == 'seed':
    db.exit2d_restore_contract.create_index('business_id', unique=True)
    db.exit2d_restore_contract.insert_one({'_id': 'immutable-fixture-id',
        'business_id': 'synthetic-job-1', 'outcome': 'unknown', 'event_id': 'fixture-event',
        'encrypted': Fernet(KEY).encrypt(b'synthetic-only-token'), 'content': Binary(BODY),
        'sha256': hashlib.sha256(BODY).hexdigest()})
elif phase == 'snapshot':
    Path(path).write_text(json.dumps(snapshot(), sort_keys=True))
    print('PASS original snapshot hashes captured without logging documents')
elif phase == 'verify':
    assert snapshot() == json.loads(Path(path).read_text()), 'restored document/index/identity hashes differ'
    row = db.exit2d_restore_contract.find_one({'_id': 'immutable-fixture-id'})
    assert row['outcome'] == 'unknown' and row['event_id'] == 'fixture-event'
    assert Fernet(KEY).decrypt(row['encrypted']) == b'synthetic-only-token'
    try:
        Fernet(Fernet.generate_key()).decrypt(row['encrypted'])
    except InvalidToken:
        pass
    else:
        raise AssertionError('wrong key accepted')
    assert hashlib.sha256(bytes(row['content'])).hexdigest() == row['sha256']
    print(json.dumps({'kind': 'fresh_compose_volume_restore', 'all_collection_document_index_hashes_equal': True,
        'collections': len(snapshot()), 'unknown_outcome_preserved': True,
        'correct_key_accepted_wrong_key_rejected': True, 'attachment_checksum_equal': True}), flush=True)
else:
    raise RuntimeError('unknown restore phase')
