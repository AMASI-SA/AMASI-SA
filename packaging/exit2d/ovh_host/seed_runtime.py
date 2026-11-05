"""Explicit fresh synthetic-host fixture; preserves owner password/MFA and providers.

Run as one-off restricted container after migration, never inside the live Web PID.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
sys.path[:0] = ['/opt/mezan/backend', '/evidence/acceptance', '/evidence/tools']

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--ack-synthetic-only', action='store_true')
    parser.add_argument('--source-sha', required=True)
    args = parser.parse_args()
    if not args.ack_synthetic_only: raise ValueError('explicit synthetic-only fixture acknowledgement required')
    for key, file in (('MONGO_URL', 'mongo_uri'), ('JWT_SECRET', 'jwt_secret')):
        os.environ[key] = Path('/run/secrets/'+file).read_text().strip()
    from independent_staging import validate
    if validate() != 'staging:'+args.source_sha: raise ValueError('source manifest differs')
    from pymongo import MongoClient
    database = MongoClient(os.environ['MONGO_URL'], serverSelectionTimeoutMS=3000)['mezan_exit2d_staging']
    if database.unified_orders.count_documents({}) or database.users.find_one({'id':'exit2c-employee'}):
        raise ValueError('fresh migrated synthetic database required; no overwrite or rerun')
    owner = database.users.find_one({'email':'admin@hesab.app', 'role':'owner'})
    if not owner: raise ValueError('separate migration must finish first')
    # Atomic once-only marker; partial failure stays blocked for deliberate disposable reset.
    database.exit2d_host_trial_control.insert_one({'_id':'seed-once', 'state':'seeding', 'classification':'synthetic-only', 'source_sha':args.source_sha})
    password = secrets.token_urlsafe(32)
    output = Path('/private-fixture/employee-credentials.json')
    descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o400)
    with os.fdopen(descriptor, 'w') as handle:
        json.dump({'email':'employee@example.com', 'password':password}, handle)
    from auth import hash_password
    database.users.insert_one({'id':'exit2c-employee','email':'employee@example.com','name':'Synthetic employee',
                               'password_hash':hash_password(password),'role':'viewer','created_by':owner['id'],'is_active':True})
    import operational_benchmark as op
    op.seed(database)
    database.unified_orders.insert_many([{'user_id':owner['id'],'order_number':f'EXIT2D-PERF-{i:05}',
        'order_date':'2026-09-06','order_status':'completed','total_amount':100,'payment_method':'Synthetic',
        'shipping_company':'Synthetic','products':[],'currency':'SAR','total_product_cost':10,'data_source':'make',
        'created_at':'2026-09-06T12:00:00+00:00'} for i in range(5000)])
    database.exit2d_host_trial_control.update_one({'_id':'seed-once'},{'$set':{'state':'ready'}})
    print(json.dumps({'classification':'synthetic-only','fixture_ready':True,'credentials_path':'private-fixture/employee-credentials.json',
                      'owner_credentials_changed':False,'provider_credentials_loaded':False}))

if __name__ == '__main__': main()
