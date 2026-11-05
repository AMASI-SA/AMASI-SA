"""CI adapter only: reuse real employee/login/80-operation tests over verified TLS."""
import asyncio
import os
import ssl
import sys
import time
sys.path[:0] = ['/opt/mezan/backend', '/evidence/acceptance', '/evidence/tools']
from independent_staging import validate
validate()
if os.environ.get('EXIT2D_ECONOMY_CI') != '1' or os.environ['MONGO_URL'] != 'mongodb://fixture:synthetic-only-password@staging-mongo:27017':
    raise ValueError('disposable CI fixture only')
import httpx
from pymongo import MongoClient
import acceptance as a
context = ssl.create_default_context(cafile='/evidence/ca.pem')
Client, AsyncClient = httpx.Client, httpx.AsyncClient
class VerifiedClient(Client):
    def __init__(self, *args, **kwargs):
        kwargs['verify'] = context; super().__init__(*args, **kwargs)
class VerifiedAsyncClient(AsyncClient):
    def __init__(self, *args, **kwargs):
        kwargs['verify'] = context; super().__init__(*args, **kwargs)
httpx.Client, httpx.AsyncClient = VerifiedClient, VerifiedAsyncClient
database = MongoClient(os.environ['MONGO_URL'])['mezan_exit2d_staging']
a.db = lambda: database; a.boundary = validate
a.URLS = ('https://127.0.0.1',) * 2; a.ORIGIN = 'https://staging.example.test'
a.JWT = os.environ['JWT_SECRET']; a.OWNER_PASSWORD = os.environ['ADMIN_PASSWORD']
import operational_benchmark as op
op.seed(database)
owner = database.users.find_one({'email': 'admin@hesab.app'})
database.users.update_one({'id': owner['id']}, {'$unset': {'mfa_last_totp_counter': ''}})
deadline = time.monotonic() + 45
while not database.exit2d_economy_sync.find_one({'_id':'snap', 'state':'running'}):
    if time.monotonic() > deadline: raise ValueError('synthetic Snap did not start')
    time.sleep(.5)
before = database.exit2d_economy_sync.find_one({'_id':'snap'})['rounds']
asyncio.run(op.run(a.owner_session(), a.employee_session({'employee':'exit2c-employee'}), database))
after = database.exit2d_economy_sync.find_one({'_id':'snap'})
if after['state'] != 'running' or after['rounds'] <= before: raise ValueError('Snap/employee overlap not proved')
import json
print(json.dumps({'kind':'verified_https_employee_snap_overlap','before_rounds':before,'after_rounds':after['rounds'],'tls_verification':True}))
