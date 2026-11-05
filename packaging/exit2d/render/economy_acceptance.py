"""Adapt existing synthetic-only controller to the actual private hosting image.

Mounted only by Linux CI, never copied to application source or used online.
All DB access is to the fixed authenticated disposable staging Mongo.
"""
import asyncio
import json
import os
import sys
import time

sys.path[:0] = ['/opt/mezan/backend', '/evidence/acceptance', '/evidence/tools']
from independent_staging import validate
validate()
assert os.environ['MONGO_URL'] == 'mongodb://fixture:synthetic-only-password@staging-mongo:27017'
assert os.environ.get('EXIT2D_ECONOMY_CI') == '1'
from pymongo import MongoClient
import acceptance as a

database = MongoClient(os.environ['MONGO_URL'], serverSelectionTimeoutMS=3000)['mezan_exit2d_staging']
a.db = lambda: database
a.boundary = validate
a.URLS = ('http://127.0.0.1:10000',) * 2
a.JWT = os.environ['JWT_SECRET']
a.OWNER_PASSWORD = os.environ['ADMIN_PASSWORD']
a.ORIGIN = 'https://staging.example.test'

def disk():
    result = database.command('dbStats')
    out = {key: result[key] for key in ('objects', 'dataSize', 'storageSize', 'indexSize', 'totalSize')}
    out['kind'] = 'synthetic_database_growth'
    print(json.dumps(out), flush=True)

if sys.argv[1:] == ['setup']:
    a.setup({})
    owner = database.users.find_one({'email': 'admin@hesab.app'})['id']
    database.unified_orders.insert_many([{
        'user_id': owner, 'order_number': f'EXIT2D-PERF-{index:05}',
        'order_date': '2026-09-06', 'order_status': 'completed', 'total_amount': 100,
        'payment_method': 'Synthetic', 'shipping_company': 'Synthetic', 'products': [],
        'currency': 'SAR', 'total_product_cost': 10, 'data_source': 'make',
        'created_at': '2026-09-06T12:00:00+00:00',
    } for index in range(5000)])
    disk()
elif sys.argv[1:] == ['load']:
    import operational_benchmark as op
    op.seed(database)
    owner = database.users.find_one({'email': 'admin@hesab.app'})
    database.users.update_one({'id': owner['id']}, {'$unset': {'mfa_last_totp_counter': ''}})
    asyncio.run(op.run(a.owner_session(), a.employee_session({'employee': 'exit2c-employee'}), database))
    disk()
elif sys.argv[1:] == ['outage']:
    session = a.employee_session({'employee': 'exit2c-employee'})
    for path in ('/api/supplier-dispatch-v1/workspace?grain=piece',
                 '/api/preparation-work-v1/assembly/orders?state=in_progress',
                 '/api/fulfillment-v2/ready-to-ship'):
        a.request(0, 'GET', path, cookie=session)
    print('PASS genuine employee operations during independent outage', flush=True)
elif sys.argv[1:] == ['growth']:
    # 50MiB incompressible synthetic attachment cohort, not a Production forecast.
    from bson.binary import Binary
    for index in range(50):
        database.exit2d_synthetic_blobs.insert_one({'id': index, 'body': Binary(os.urandom(1024*1024))})
    disk()
else:
    raise RuntimeError('unknown bounded economy phase')
